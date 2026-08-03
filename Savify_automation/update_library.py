import json
import subprocess
import time
from colorama import init, Fore
import music_tag
import os

init(autoreset=True)

# Variables
spotify_playlists = json.loads(open("playlists.json").read())
LIBRARY_PATH = spotify_playlists["library-path"]


def download(naem, dest, url):
	print(Fore.LIGHTBLUE_EX + "Downloading " + Fore.YELLOW + naem + Fore.LIGHTBLUE_EX + "...")
	subcmd = str("python .\\savify-new\\savify\\__main__.py -o \"" + dest + "\" " + url)
	subprocess.run(subcmd)
	time.sleep(0.5)


def tag_genre(dest, genre):
	print(Fore.LIGHTBLUE_EX + "setting tags...")
	for filename in os.listdir(dest):
		if filename[-4:] == ".mp3":
			pth = os.path.join(dest, filename)
			metadata = music_tag.load_file(pth)
			metadata["genre"] = str(genre)
			metadata.save()
	print(Fore.GREEN + "...tags set.")
	

# Main Process
for x in spotify_playlists:
	if x != "library-path" and x != "playlist-name":
		try:
			download(naem=str(x), dest= LIBRARY_PATH + str(x["path"]), url=str(x["url"]))
			if str(x["genre"]) != "":
				tag_genre(dest= LIBRARY_PATH + str(x["path"]), genre=str(x["genre"]))
			print(Fore.GREEN + "\nDownload of " + Fore.YELLOW + str(x) + Fore.LIGHTBLUE_EX + " completed.\n\n")
			time.sleep(3)
		except Exception as e:
			print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")

print(Fore.GREEN + "\n\nLibrary update completed.\n\n")
input(Fore.LIGHTBLUE_EX + "Press ENTER to quit.")
print(Fore.LIGHTMAGENTA_EX + "\n\n\n\n\n\nSee ya!\n\n\n\n\n\n")
time.sleep(1)
