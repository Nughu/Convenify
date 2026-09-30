import subprocess
import os
import json
import re
from time import sleep
from colorama import Fore, Style, init
from pathlib import Path
import music_tag
from setup_check import ensure_requirements

init(autoreset=True)

# Variables
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
ensure_requirements(ROOT_DIR)
PYTHON_PATH = str(ROOT_DIR / "python" / "python.exe")
SAVIFY_PATH = str(ROOT_DIR / "savify-new")

LIBRARY_PATH = Path(json.loads(open(str(ROOT_DIR / "config.json")).read())["library_path"])
for subdir in ("Playlist", "Track", "Album"):
    (LIBRARY_PATH / subdir).mkdir(parents=True, exist_ok=True)

track_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["track_args"]
album_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["album_args"]
playlist_args = json.loads(open(str(ROOT_DIR / "config.json")).read())["playlist_args"]

GENRE_PATTERN = re.compile(r'genre:"([^"]*)"')
SPOTIFY_URL_PATTERN = re.compile(r'https?://open\.spotify\.com/\S+')


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


def _extract_genre(value):

	match = GENRE_PATTERN.search(value)
	if not match:
		return "", value.strip(), ""
	return match.group(1), value[:match.start()].strip(), value[match.end():].strip()


def _parse_queue_lines(lines):
	parsed = []
	block_genre = None
	for line_number, raw_line in enumerate(lines, start=1):
		line = raw_line.strip()
		if not line or line.startswith("#"):
			block_genre = None
			continue

		genre_match = GENRE_PATTERN.fullmatch(line)
		if genre_match:
			block_genre = genre_match.group(1)
			continue

		genre_match = GENRE_PATTERN.search(line)
		genre, before_genre, after_genre = _extract_genre(line)
		url_match = SPOTIFY_URL_PATTERN.search(line)
		if not url_match:
			continue

		url = url_match.group(0).replace("intl-de/", "")
		line_genre = genre if genre_match and before_genre and not after_genre else block_genre
		parsed.append((line_number, url, line_genre or ""))
	return parsed


def tag_genre(files, genre):
	if not genre:
		return
	print(Fore.LIGHTBLUE_EX + "setting tags...")
	for path in files:
		metadata = music_tag.load_file(path)
		metadata["genre"] = str(genre)
		metadata.save()
	print(Fore.GREEN + "...tags set.")


def _new_mp3_files(destination, existing_files):
	return [
		path
		for path in destination.rglob("*")
		if path.is_file()
		and path.suffix.lower() == ".mp3"
		and path.resolve() not in existing_files
	]


def _download_to_destination(url, destination, savify_args, genre, download_type, show_output):
	destination.mkdir(parents=True, exist_ok=True)
	# Include nested album/artist folders in the before-download snapshot.
	existing_files = {
		path.resolve()
		for path in destination.rglob("*")
		if path.is_file() and path.suffix.lower() == ".mp3"
	}
	subcmd = (
		f'"{PYTHON_PATH}" -m savify '
		f'-o "{destination}" '
		f'--clear-console '
		f'{savify_args} '
		f'{url}'
	)
	returncode, result_output = run_savify_command(subcmd, show_output=show_output)
	failed_tracks = _parse_failed_tracks(result_output)
	new_files = _new_mp3_files(destination, existing_files)
	if genre:
		tag_genre(new_files, genre)
	result = {
		"url": url,
		"type": download_type,
		"returncode": returncode,
		"failed_tracks": failed_tracks,
	}
	if not failed_tracks:
		print(Fore.GREEN + "Download completed.")
	else:
		print(Fore.RED + f"Download finished with {len(failed_tracks)} failed track(s).")
	return result


def print_batch_summary(results):
    if not results:
        print(Fore.YELLOW + "No links to summarize.")
        return

    link_number_width = len(str(max(item["link_number"] for item in results)))
    print(f"\n{Fore.MAGENTA}======= Final batch download report ======={Fore.RESET}\n")
    for item in results:
        link_number = item["link_number"]
        file_line_number = item.get("file_line_number")
        url = item["url"]
        failed_tracks = item["result"].get("failed_tracks", [])

        if file_line_number is not None:
            location_label = f"{Fore.RESET}Link {link_number:>{link_number_width}}  (line {Fore.YELLOW}{file_line_number:>3}{Fore.RESET})"
        else:
            location_label = f"{Fore.RESET}Link {link_number:>{link_number_width}}"

        if not failed_tracks:
            print(f"{location_label:<26}: {Fore.GREEN}OK")
            continue

        print(f"{location_label:<26}: {Fore.RED}FAILED")
        for failed in failed_tracks:
            print(Fore.RED + f"  - {failed['song']}")
            print(Fore.RED + f"    {failed['reason']}{Fore.RESET}")

    print(Fore.MAGENTA + "\n===========================================" + Fore.RESET + "\n")


def run_savify_command(subcmd, show_output=True):
    process = subprocess.Popen(
        subcmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
		encoding="utf-8",
		errors="replace",
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


def download(url, genre="", show_output=True):

	# Playlist
	if (url[25:33]) == "playlist":
		Type = "Playlist"
		return _download_to_destination(url, LIBRARY_PATH / Type, playlist_args, genre, Type, show_output)

	# Track
	if (url[25:30]) == "track":
		Type = "Track"
		return _download_to_destination(url, LIBRARY_PATH / Type, track_args, genre, Type, show_output)

	# Album
	if (url[25:30]) == "album":
		Type = "Album"
		return _download_to_destination(url, LIBRARY_PATH / Type, album_args, genre, Type, show_output)

	# Neither
	if (url[25:33]) != "playlist" and (url[25:30]) != "track" and (url[25:30]) != "album":
		Type = "Other"
		print(Fore.RED + "Error in link resolve:")
		print((Fore.RESET + url) + "\n" + (Fore.RED + "                         I"))
		conanyway = ""
		while conanyway not in ["Y", "y", "Yes", "yes", "YES", "N", "n", "No", "no", "NO"]:
			conanyway = input(Fore.LIGHTBLUE_EX + "\ntry anyway? (Y/N)\n")
		if conanyway in ["Y", "y", "Yes", "yes", "YES"]:
			print((Fore.YELLOW + "\nType: ") + (Fore.LIGHTGREEN_EX + Type) + (Fore.LIGHTBLUE_EX + "\n\n\nlaunching Savify..."))
			return _download_to_destination(url, LIBRARY_PATH / Type, "", genre, Type, show_output)
		if conanyway in ["N", "n", "No", "no", "NO"]:
			print(Fore.RESET + Style.DIM + "Download aborted.")
			Style.RESET()
		return {"url": url, "type": Type, "returncode": 1, "failed_tracks": [{"song": url, "reason": "Download aborted by user."}]}


def main():
	while True:
		try:
			print(f"{Fore.LIGHTBLUE_EX}Enter Spotify link (or .txt file containing multiple links).{Fore.RESET}")
			userinput = input(Fore.RESET + "")
			if userinput.lower() == "queue" or userinput.lower() == "q":
				userinput = "download-queue.txt"
			if userinput[-4:] == ".txt":
				with open(userinput, encoding="utf-8", errors="replace") as file:
					valid_links = _parse_queue_lines(file.readlines())
					batch_results = []
					for processed_count, (line_number, y, genre) in enumerate(valid_links, start=1):
						clear_console()
						print(f"{Fore.LIGHTBLUE_EX}Downloading from {userinput}...{Fore.RESET}\n{Fore.YELLOW}{str(processed_count)} / {str(len(valid_links))}{Fore.RESET}")
						sleep(1)
						try:
							result = download(y, genre=genre, show_output=True)
						except Exception as error:
							print(Fore.RED + f"Download failed unexpectedly: {error}")
							result = {
								"url": y,
								"type": "Unknown",
								"returncode": 1,
								"failed_tracks": [{"song": y, "reason": str(error)}],
							}
						batch_results.append({"link_number": processed_count, "file_line_number": line_number, "url": y, "result": result})
					clear_console()
					print_batch_summary(batch_results)
			else:
				genre, url, trailing_text = _extract_genre(userinput.replace("intl-de/", ""))
				if url[13:20] == "spotify" and not trailing_text:
					download(url, genre=genre)
				else:
					print(Fore.RED + "\n############################\nNOT UNDERSTOOD!\n############################\n")
			sleep(1)
			input(Fore.LIGHTBLUE_EX + "\nPress Enter to continue...")
			clear_console()
		except Exception as e:
			print(Fore.RED + f"\n\n############################\n\n{e}\n\n############################\n\n")
			input(Fore.LIGHTBLUE_EX + "Press Enter to continue...")


if __name__ == "__main__":
	main()
