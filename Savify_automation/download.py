import subprocess
from time import sleep
import json
from colorama import Fore, Style, init
from pathlib import Path

init(autoreset=True)

# Variables
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
PYTHON_PATH = str(ROOT_DIR / "python" / "python.exe")
SAVIFY_PATH = str(ROOT_DIR / "savify-new")

LIBRARY_PATH = json.loads(open(str(ROOT_DIR / "config.json")).read())["library_path"]

track_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["track_args"]
album_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["album_args"]
playlist_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["playlist_args"]

# yt-dlp args, probably don't work in savify directly
#additional_args = " --cookies-from-browser chrome --sleep-requests 1.25 --min-sleep-interval 60 --max-sleep-interval 90"
#extractor_args = "--extractor-args \"youtube:player-client=default,-tv_simply\""
#extractor_args = "\"youtube:player_client=ios\" -f \"bv[protocol=m3u8_native]+ba[protocol=m3u8_native]\""


def download(url):

	# Playlist
	if (url[25:33]) == "playlist":
		Type = "Playlist"
		subcmd = str(
			f"\"{PYTHON_PATH}\" -m savify "
			f"-o \"{LIBRARY_PATH}\\Playlist\" "
			f"{playlist_args} "
			f"{url}"
			)
		
		sub = subprocess.Popen(subcmd, shell=True)
		sub.wait()
		print(Fore.GREEN + "Download completed.")

	# Track
	if (url[25:30]) == "track":
		Type = "Track"
		
		subcmd = (
			f"\"{PYTHON_PATH}\" -m savify "
			f"-o \"{LIBRARY_PATH}\\Track\" "
			f"{track_args} "
			f"{url}"
			)
		
		sub = subprocess.Popen(subcmd, shell=True)
		sub.wait()
		print(Fore.GREEN + "Download completed.")

	# Album
	if (url[25:30]) == "album":
		Type = "Album"
		subcmd = str(
			f"\"{PYTHON_PATH}\" -m savify "
			f"-o \"{LIBRARY_PATH}\\Album\" "
			f"{album_args} "
			f"{url}"
			)
		
		sub = subprocess.Popen(subcmd, shell=True)
		sub.wait()
		print(Fore.GREEN + "Download completed.")

	# Neither
	if (url[25:33]) != "playlist" and (url[25:30]) != "track" and (url[25:30]) != "album":
		Type = "Other"
		print(Fore.RED + "Error in link resolve:")
		print((Fore.RESET + url) + "\n" + (Fore.RED + "                         I"))
		conanyway = ""
		while conanyway not in ["Y", "y", "Yes", "yes", "YES", "N", "n", "No", "no", "NO"]:
			conanyway = input(Fore.LIGHTBLUE_EX + "\ntry anyway? (Y/N)")
		if conanyway in ["Y", "y", "Yes", "yes", "YES"]:
			print((Fore.YELLOW + "\nType: ") + (Fore.LIGHTGREEN_EX + Type) + (Fore.LIGHTBLUE_EX + "\n\n\nlaunching Savify..."))
			sub = subprocess.Popen(subcmd, shell=True)
			sub.wait()
			print(Fore.GREEN + "Download completed.")
		if conanyway in ["N", "n", "No", "no", "NO"]:
			print(Fore.RESET + Style.DIM + "Download aborted.")
			Style.RESET()


# Main loop
while True:
	try:
		print(Fore.LIGHTBLUE_EX + "Enter Spotify link (or .txt file containing multiple links).")
		userinput = ((input(Fore.RESET + "")).replace("intl-de/", ""))
		if userinput == "queue":
			userinput = "download-queue.txt"
		if userinput[-4:] == ".txt":
			with open(userinput) as file:
				read = file.readlines()
				for x in read:
					y = x.replace("\n", "").replace(" ", "")
					if y != "" and not y.startswith("#"):
						print(Fore.LIGHTBLUE_EX + "\nDownloading from " + userinput + "...\n" + Fore.YELLOW + (str((read.index(x)) + 1) + " / " + str(len(read))))
						sleep(1)
						download(y)
		elif userinput[13:20] == "spotify":
			download(userinput)
		else:
			print(Fore.RED + "\n############################\nNOT UNDERSTOOD!\n############################\n")
			input("")
		print(Fore.RESET + "\n\n\n")
		sleep(1)
	except Exception as e:
		print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")
		input("")
