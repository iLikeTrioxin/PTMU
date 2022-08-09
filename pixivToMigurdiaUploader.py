import datetime
from FileBlackHolePy import FileBlackHole, initLib, destroyLib
from MigurdiaPy      import Migurdia
from json            import dumps, loads
from colors          import log, bcolors
from PIL             import Image
from credentials     import __USERNAME__, __PASSWORD__, __TOKEN__
from os.path         import getsize, isfile, isdir
from pixivpy_async   import AppPixivAPI
from os              import remove, rename, mkdir
from shutil          import copyfile
from random          import randint

import aiohttp
import asyncio


tempFolder        = f"./temp-{datetime.datetime.now()}"
migurdiaSessionID = ""


async def isValidImageFile(path):
    try:
        Image.open(path).verify()
    except:
        return False

    return True


async def getThumbnail(path):
    if not await isValidImageFile(path):
        copyfile(path, path + "bc")
        log(f"[!] File {path} is not a valid image.", [bcolors.FAIL])
        return None

    image = Image.open(path)

    pixelArea   = image.size[0] * image.size[1]
    aspectRatio = image.size[0] / image.size[1]

    # return path as it is small itself
    if getsize(path) < 512000: return path

    # begin creating thumbnail by creating path for it
    path = path.split('.')
    path = '.'.join( path[:-1] + ["thumbnail", path[-1]] )

    # convert to jpeg
    image = image.convert('RGB')

    # if res is small, save it as jpeg and return
    if pixelArea <= (512 * 512):
        image.save( path )
        return path

    # calculate new size for image (keeping aspect ratio)
    newWidth  = ((512 * 512) * aspectRatio) ** 0.5
    newHeight =  (512 * 512) / newWidth

    size = (int(newWidth), int(newHeight))

    image = image.resize(size)
    image.save(path)

    return path


class PixivScraper(Migurdia):
    downloader = None
    pixivApp   = None

    def __init__(self):
        super().__init__()

        connector = aiohttp.TCPConnector(limit=4)
        self.downloader = aiohttp.ClientSession(connector=connector)
        self.pixivApp   = AppPixivAPI()

    async def quit(self):
        await self.downloader.close()

    async def login(self, migurdiaUsername, migurdiaPassword, pixivRefreshToken):
        await self.pixivApp.login(refresh_token=pixivRefreshToken)
        await super().login(migurdiaUsername, migurdiaPassword)

    async def downloadFile(self, url, tries=6):
        if tries == 0: return None

        localFilename = f"{tempFolder}/{url.split('/')[-1]}"

        if isfile(localFilename): remove(localFilename)

        hs = {
            'referer'      : 'https://www.pixiv.net/',
            'cache-control': 'no-cache',
            'pragma'       : 'no-cache'
        }

        try:
            async with self.downloader.get(url, headers=hs) as response:
                with open(localFilename, 'wb+') as file:
                    file.write( await response.read() )

            if not await isValidImageFile(localFilename):
                remove(localFilename)
                raise Exception("Downloaded file is invalid.")
        except:
            x = random.randint(5, 100)
            log(f"[!] Failed to download file ({tries} tries left). Waiting {x} seconds.", [bcolors.WARNING])
            await asyncio.sleep(x)
            return await self.downloadFile(url, tries - 1)
        return localFilename

    async def addPixivFile(self, fb, fileUrl, title, desc, tags):
        path = await self.downloadFile(fileUrl)

        if path is None:
            log(f"[!] Failed to download file {fileUrl}.", [bcolors.FAIL])
            return None

        thumbnailPath = await getThumbnail(path)
        if thumbnailPath != path: remove(path)

        if thumbnailPath is None:
            log(f"[!] Failed to create thumbnail of {path}.", [bcolors.FAIL])
            return None

        thumbnailCode = await fb.uploadFile(thumbnailPath)
        remove(thumbnailPath)

        if thumbnailCode is None or thumbnailCode['exitCode'] != 0:
            log(f"[!] Failed to upload thumbnail of {path}.", [bcolors.FAIL])
            return None

        result = await super().addPost(
            fileUrl,
            f"https://fileblackhole.000webhostapp.com/files/{thumbnailCode['result']}",
            tags,
            title,
            desc
        )

        if result             is None: log(f"[!] Failed to process file {path}", [bcolors.FAIL])
        if result['exitCode'] !=    0:
            log(f"[!] Failed to add post for {path}. (CODE: {result['exitCode']})", [bcolors.FAIL])

        return result['result'][0]['result']

    async def addPixivPost(self, fb, post):
        log(f"[*] Processing post (ID: {post.id}).", [bcolors.OKCYAN])

        urls = []

        if post.page_count > 1: urls = [ post.image_urls.original for post in post.meta_pages ]
        else:                   urls = [ post.meta_single_page.original_image_url             ]

        tags = [ tag.name for tag in post.tags ]
        tags.append(post.user.name)

        tasks = []
        for url in urls:
            tasks.append( self.addPixivFile(fb, url, post.title, post.caption, tags) )

        result = await asyncio.gather(*tasks)

        log(f"[*] Successfully processed post id {post.id}", [bcolors.OKGREEN])

        for r in result:
            if r is None:
                with open(f"{tempFolder}/{post.id}", 'w+') as f:
                    f.write( dumps(result) )

    async def scrapePixivAuthor(self, authorID):
        log(f"[*] Scraping author id {authorID}", [bcolors.OKCYAN])

        fileBlackHole = FileBlackHole()
        await fileBlackHole.createSession()

        tasksPA   = []
        next_qs = { 'user_id': authorID }
        while True:
            result = await self.pixivApp.user_illusts(**next_qs)

            if result.illusts is None: break

            for illust in result.illusts:
                tasksPA.append( self.addPixivPost(fileBlackHole, illust) )

            next_qs = self.pixivApp.parse_qs(result.next_url)

            if next_qs is None: break

        await asyncio.gather(*tasksPA)
        await fileBlackHole.close()

        log(f"[*] Successfully scraped author id {authorID}", [bcolors.OKGREEN])


if not isdir(tempFolder):
    mkdir(tempFolder)


async def main():
    with open("final.json") as f:
        authors = loads(f.read())

    await initLib()
    scraper = PixivScraper()
    await scraper.login(__USERNAME__, __PASSWORD__, __TOKEN__)

    tasksA = []
    for i in range(len(authors)):
        if i % 3 == 0:
            await asyncio.gather(*tasksA)
            tasksA = []

        tasksA.append( scraper.scrapePixivAuthor(int(authors[i])) )

    await scraper.quit()
    await destroyLib()


if __name__ == "__main__":
    asyncio.run(main())
