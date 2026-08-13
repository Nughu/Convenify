import subprocess
import os
import json
from time import sleep
from colorama import Fore, Style, init
from pathlib import Path

init(autoreset=True)

# Variables
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
PYTHON_PATH = str(ROOT_DIR / "python" / "python.exe")
SAVIFY_PATH = str(ROOT_DIR / "savify-new")

LIBRARY_PATH = Path(json.loads(open(str(ROOT_DIR / "config.json")).read())["library_path"])
for subdir in ("Playlist", "Track", "Album"):
    (LIBRARY_PATH / subdir).mkdir(parents=True, exist_ok=True)

track_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["track_args"]
album_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["album_args"]
playlist_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["playlist_args"]


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
        elif stripped.startswith("Reason:"):
            if current_song:
                failed.append({
                    "song": current_song,
                    "reason": stripped.split("Reason:", 1)[1].strip(),
                })
                current_song = None
    return failed


def print_batch_summary(results):
    if not results:
        print(Fore.YELLOW + "No links to summarize.")
        return

    print(f"\n{Fore.MAGENTA}===== {Fore.RESET}Final batch download report{Fore.MAGENTA} ====={Fore.RESET}\n")
    for item in results:
        link_number = item["link_number"]
        file_line_number = item.get("file_line_number")
        url = item["url"]
        failed_tracks = item["result"].get("failed_tracks", [])

        if file_line_number is not None:
            location_label = f"{Fore.RESET}Link {link_number:>2}  ({Fore.YELLOW}line {file_line_number:>2}{Fore.RESET})"
        else:
            location_label = f"{Fore.RESET}Link {link_number:>2}"

        if not failed_tracks:
            print(f"{location_label:<26}: {Fore.GREEN}OK")
            continue

        print(f"{location_label:<26}: {Fore.RED}FAILED")
        for failed in failed_tracks:
            print(Fore.RED + f"  - {failed['song']}")
            print(Fore.RED + f"    {failed['reason']}{Fore.RESET}")

    print(Fore.MAGENTA + "\n=======================================" + Fore.RESET + "\n")


def run_savify_command(subcmd, show_output=True):
    process = subprocess.Popen(
        subcmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    combined_output = []
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if line:
            text = line.rstrip("\n")
            combined_output.append(text)
            if show_output:
                print(text)

    remaining_output = process.stdout.read()
    if remaining_output:
        remaining_output = remaining_output.rstrip("\n")
        combined_output.extend(remaining_output.splitlines())
        if show_output:
            print(remaining_output, end="")

    return process.wait(), "\n".join(combined_output).strip()


def download(url, show_output=True):

	# Playlist
	if (url[25:33]) == "playlist":
		Type = "Playlist"
		subcmd = str(
			f"\"{PYTHON_PATH}\" -m savify "
			f"-o \"{LIBRARY_PATH / 'Playlist'}\" "
			f"{playlist_args} "
			f"{url}"
			)
		
		returncode, result_output = run_savify_command(subcmd, show_output=show_output)
		failed_tracks = _parse_failed_tracks(result_output)
		result = {
			"url": url,
			"type": Type,
			"returncode": returncode,
			"failed_tracks": failed_tracks,
		}
		if not failed_tracks:
			print(Fore.GREEN + "Download completed.")
		else:
			print(Fore.RED + f"Download finished with {len(failed_tracks)} failed track(s).")
		return result

	# Track
	if (url[25:30]) == "track":
		Type = "Track"
		
		subcmd = (
			f"\"{PYTHON_PATH}\" -m savify "
			f"-o \"{LIBRARY_PATH / 'Track'}\" "
			f"{track_args} "
			f"{url}"
			)
		
		returncode, result_output = run_savify_command(subcmd, show_output=show_output)
		failed_tracks = _parse_failed_tracks(result_output)
		result = {
			"url": url,
			"type": Type,
			"returncode": returncode,
			"failed_tracks": failed_tracks,
		}
		if not failed_tracks:
			print(Fore.GREEN + "Download completed.")
		else:
			print(Fore.RED + f"Download finished with {len(failed_tracks)} failed track(s).")
		return result

	# Album
	if (url[25:30]) == "album":
		Type = "Album"
		subcmd = str(
			f"\"{PYTHON_PATH}\" -m savify "
			f"-o \"{LIBRARY_PATH / 'Album'}\" "
			f"{album_args} "
			f"{url}"
			)
		
		returncode, result_output = run_savify_command(subcmd, show_output=show_output)
		failed_tracks = _parse_failed_tracks(result_output)
		result = {
			"url": url,
			"type": Type,
			"returncode": returncode,
			"failed_tracks": failed_tracks,
		}
		if not failed_tracks:
			print(Fore.GREEN + "Download completed.")
		else:
			print(Fore.RED + f"Download finished with {len(failed_tracks)} failed track(s).")
		return result

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
			returncode, result_output = run_savify_command(subcmd, show_output=show_output)
			failed_tracks = _parse_failed_tracks(result_output)
			result = {
				"url": url,
				"type": Type,
				"returncode": returncode,
				"failed_tracks": failed_tracks,
			}
			if not failed_tracks:
				print(Fore.GREEN + "Download completed.")
			else:
				print(Fore.RED + f"Download finished with {len(failed_tracks)} failed track(s).")
			return result
		if conanyway in ["N", "n", "No", "no", "NO"]:
			print(Fore.RESET + Style.DIM + "Download aborted.")
			Style.RESET()
		return {"url": url, "type": Type, "returncode": 1, "failed_tracks": [{"song": url, "reason": "Download aborted by user."}]}


# Main loop
while True:
	try:
		print(f"{Fore.LIGHTBLUE_EX}Enter Spotify link (or .txt file containing multiple links).{Fore.RESET}")
		userinput = ((input(Fore.RESET + "")).replace("intl-de/", ""))
		if userinput.lower() == "queue" or userinput.lower() == "q":
			userinput = "download-queue.txt"
		if userinput[-4:] == ".txt":
			with open(userinput) as file:
				read = file.readlines()
				valid_links = []
				for line_number, x in enumerate(read, start=1):
					y = x.replace("\n", "").replace(" ", "").replace("intl-de/", "")
					if y != "" and not y.startswith("#"):
						valid_links.append((line_number, y))
				batch_results = []
				for processed_count, (line_number, y) in enumerate(valid_links, start=1):
					clear_console()
					print(f"{Fore.LIGHTBLUE_EX}Downloading from {userinput}...{Fore.RESET}\n{Fore.YELLOW}{str(processed_count)} / {str(len(valid_links))}{Fore.RESET}")
					sleep(1)
					result = download(y, show_output=True)
					batch_results.append({"link_number": processed_count, "file_line_number": line_number, "url": y, "result": result})
				clear_console()
				print_batch_summary(batch_results)
		elif userinput[13:20] == "spotify":
			download(userinput)
		else:
			print(Fore.RED + "\n############################\nNOT UNDERSTOOD!\n############################\n")
			input("")
		sleep(1)
		input(Fore.LIGHTBLUE_EX + "\nPress Enter to continue...")
		clear_console()
	except Exception as e:
		print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n\n")
		input("")
