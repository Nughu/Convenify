import subprocess
from time import sleep
from colorama import Fore, Style, init

init(autoreset=True)

# Variables
musicpath = "root-path"
savify_path = ".\\savify-new"

track_args = "-g \"%artist%\""
album_args = "-g \"%artist%/%album%\""
playlist_args = "-g \"%playlist%\""

additional_args = " --cookies-from-browser chrome --sleep-requests 1.25 --min-sleep-interval 60 --max-sleep-interval 90"

#extractor_args = "--extractor-args \"youtube:player-client=default,-tv_simply\""
#extractor_args = "\"youtube:player_client=ios\" -f \"bv[protocol=m3u8_native]+ba[protocol=m3u8_native]\""

def download(url):
	if (url[25:33]) == "playlist":
		Type = "Playlist"
		subcmd = str("python \"" + savify_path + "\\savify\\__main__.py\" -o \"" + musicpath + "\\Playlist\" " + playlist_args + " " + url)
		sub = subprocess.Popen(subcmd, shell=True)
		sub.wait()
		print(Fore.GREEN + "Download completed.")
	if (url[25:30]) == "track":
		Type = "Track"
		subcmd = str("python \"" + savify_path + "\\savify\\__main__.py\" -o \"" + musicpath + "\" " + track_args + " " + url)
		sub = subprocess.Popen(subcmd, shell=True)
		sub.wait()
		print(Fore.GREEN + "Download completed.")
	if (url[25:30]) == "album":
		Type = "Album"
		subcmd = str("python \"" + savify_path + "\\savify\\__main__.py\" -o \"" + musicpath + "\\Album\" " + album_args + " " + url)
		sub = subprocess.Popen(subcmd, shell=True)
		sub.wait()
		print(Fore.GREEN + "Download completed.")
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
					y = x.replace("\n", "")
					print(Fore.LIGHTBLUE_EX + "\nDownloading from " + userinput + "...\n" + Fore.YELLOW + (str((read.index(x)) + 1) + " / " + str(len(read))))
					sleep(1)
					download(y)	
		elif userinput[13:20] == "spotify":
			download(userinput)
			print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")
		
		else:
			print(Fore.RED + "\n############################\nNOT UNDERSTOOD!\n############################\n")
		print(Fore.RESET + "\n\n\n")
		sleep(1)
	except Exception as e:
		print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")


#input(Fore.LIGHTBLUE_EX + "Press ENTER to quit.")
#print(Fore.LIGHTMAGENTA_EX + "\n\n\n\n\n\nSee ya!\n\n\n\n\n\n")
