import json
import os
import argparse
from pathlib import Path
import subprocess
import time
from colorama import init, Fore
import music_tag

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

parser = argparse.ArgumentParser()
parser.add_argument(
	"--report-failures",
		action="store_true",
		help="print a final report of downloads that failed",
)
args = parser.parse_args()
	

def clear_console():
	if os.name == "nt":
		subprocess.run(["cmd", "/c", "cls"], check=False, shell=False)
	elif os.name == "posix":
		subprocess.run(["clear"], check=False)
	else:
		print("\n" * 50)


def _parse_failed_tracks(output):
	failed = []
	if not output or "Failed Tracks:" not in output:
		return failed

	current_song = None
	for line in output.splitlines():
		stripped = line.strip()
		if not stripped:
			continue
		if stripped.startswith("Song:"):
			current_song = stripped.split("Song:", 1)[1].strip()
		elif stripped.startswith("Reason:") and current_song:
			failed.append({
				"song": current_song,
				"reason": stripped.split("Reason:", 1)[1].strip(),
			})
			current_song = None
	return failed


def download(naem, dest, url):
	print(Fore.LIGHTBLUE_EX + "Downloading " + Fore.YELLOW + naem + Fore.LIGHTBLUE_EX + "...")
	subcmd = str(
		f"\"{PYTHON_PATH}\" -m savify "
		f"-o \"{dest}\" "
        f"{url}"
		)
	sub = subprocess.Popen(
		subcmd,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		bufsize=1,
	)
	output_lines = []
	for line in sub.stdout:
		print(line, end="")
		output_lines.append(line.rstrip("\n"))
	returncode = sub.wait()
	time.sleep(0.5)
	failed_tracks = _parse_failed_tracks("\n".join(output_lines))
	if returncode != 0 and not failed_tracks:
		failed_tracks.append({
			"song": naem,
			"reason": f"Savify exited with code {returncode}.",
		})
	return {
		"name": naem,
		"failed_tracks": failed_tracks,
	}


def tag_genre(files, genre):
	print(Fore.LIGHTBLUE_EX + "setting tags...")
	for pth in files:
		metadata = music_tag.load_file(pth)
		metadata["genre"] = str(genre)
		metadata.save()
	print(Fore.GREEN + "...tags set.")


def print_failure_report(results):
	failed_results = [result for result in results if result["failed_tracks"]]
	print(Fore.MAGENTA + "\n======= Final library update report =======\n" + Fore.RESET)
	if not failed_results:
		print(Fore.GREEN + "All downloads completed successfully.")
	else:
		for result in failed_results:
			print(Fore.RED + f"{result['name']}: FAILED")
			for failed in result["failed_tracks"]:
				print(Fore.RED + f"  - {failed['song']}")
				print(Fore.RED + f"    {failed['reason']}{Fore.RESET}")
	print(Fore.MAGENTA + "\n===========================================\n" + Fore.RESET)
	

# Main Process
download_results = []
for x in spotify_playlists:
	if x != "library-path" and x != "playlist-name":
		try:
			playlist_path = LIBRARY_PATH / spotify_playlists[x]["path"]
			if not playlist_path.exists():
				playlist_path.mkdir(parents=True, exist_ok=True)
			existing_files = {
				path.resolve()
				for path in playlist_path.iterdir()
				if path.is_file() and path.suffix.lower() == ".mp3"
			}
			result = download(naem=x, dest=playlist_path, url=spotify_playlists[x]["link"])
			download_results.append(result)
			if spotify_playlists[x]["genre"] != "":
				new_files = [
					path
					for path in playlist_path.iterdir()
					if path.is_file()
					and path.suffix.lower() == ".mp3"
					and path.resolve() not in existing_files
				]
				if new_files:
					tag_genre(files=new_files, genre=spotify_playlists[x]["genre"])
			print(Fore.GREEN + "\nDownload of " + Fore.YELLOW + str(x) + Fore.LIGHTBLUE_EX + " completed.\n\n")
			time.sleep(3)
			clear_console()
		except Exception as e:
			download_results.append({
				"name": x,
				"failed_tracks": [{"song": x, "reason": str(e)}],
			})
			print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")

if args.report_failures:
	print_failure_report(download_results)

print(Fore.GREEN + "\n\nLibrary update completed.\n\n")
input(Fore.LIGHTBLUE_EX + "Press ENTER to quit.")
print(Fore.LIGHTMAGENTA_EX + "\n\n\n\n\n\nSee ya!\n\n\n\n\n\n")
time.sleep(1)
