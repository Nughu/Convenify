import json
from pathlib import Path
import subprocess
import time
from colorama import init, Fore
import music_tag
import os

init(autoreset=True)

# Variables
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
PYTHON_PATH = str(ROOT_DIR / "python" / "python.exe")
SAVIFY_PATH = str(ROOT_DIR / "savify-new")
LIBRARY_PATH = Path(json.loads(open(str(ROOT_DIR / "config.json")).read())["library_path"])
for subdir in ("Playlist", "Track", "Album"):
    (LIBRARY_PATH / subdir).mkdir(parents=True, exist_ok=True)

spotify_playlists = json.loads(open(str(ROOT_DIR / "playlists.json")).read())
	

def download(naem, dest, url):
	print(Fore.LIGHTBLUE_EX + "Downloading " + Fore.YELLOW + naem + Fore.LIGHTBLUE_EX + "...")
	subcmd = str(
		f"\"{PYTHON_PATH}\" -m savify "
		f"-o \"{dest}\" "
        f"{url}"
		)
	sub = subprocess.Popen(subcmd, shell=True)
	sub.wait()
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
			if not os.path.exists(LIBRARY_PATH + spotify_playlists[x]["path"]):
				os.makedirs(LIBRARY_PATH + spotify_playlists[x]["path"])
			download(naem=x, dest= LIBRARY_PATH + spotify_playlists[x]["path"], url=spotify_playlists[x]["link"])
			if spotify_playlists[x]["genre"] != "":
				tag_genre(dest= LIBRARY_PATH + spotify_playlists[x]["path"], genre=spotify_playlists[x]["genre"])
			print(Fore.GREEN + "\nDownload of " + Fore.YELLOW + str(x) + Fore.LIGHTBLUE_EX + " completed.\n\n")
			time.sleep(3)
		except Exception as e:
			print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")

print(Fore.GREEN + "\n\nLibrary update completed.\n\n")
input(Fore.LIGHTBLUE_EX + "Press ENTER to quit.")
print(Fore.LIGHTMAGENTA_EX + "\n\n\n\n\n\nSee ya!\n\n\n\n\n\n")
time.sleep(1)
