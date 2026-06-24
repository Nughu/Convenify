import subprocess
import time
from colorama import init, Fore
import music_tag
import os
import sys

init(autoreset=True)

# Variables
LIBRARY_PATH = "root-path"

# Playlists
spotify_playlists = [
	["playlist-name", LIBRARY_PATH + "\\sub-path", "link", "genre"]
]

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
	try:
		download(naem=str(x[0]), dest=str(x[1]), url=str(x[2]))
		if str(x[3]) != "":
			tag_genre(dest=str(x[1]), genre=str(x[3]))
		print(Fore.GREEN + "\nDownload of " + Fore.YELLOW + str(x[0]) + Fore.LIGHTBLUE_EX + " completed.\n\n")
		time.sleep(3)
	except Exception as e:
		print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")

print(Fore.GREEN + "\n\nLibrary update completed.\n\n")
input(Fore.LIGHTBLUE_EX + "Press ENTER to quit.")
print(Fore.LIGHTMAGENTA_EX + "\n\n\n\n\n\nSee ya!\n\n\n\n\n\n")
time.sleep(1)
