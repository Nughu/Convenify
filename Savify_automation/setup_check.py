import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def _missing_requirements(root_dir):
	missing = []
	if not os.environ.get("SPOTIPY_CLIENT_ID", "").strip():
		missing.append("Spotify client ID")
	if not os.environ.get("SPOTIPY_CLIENT_SECRET", "").strip():
		missing.append("Spotify client secret")
	if not shutil.which("deno"):
		missing.append("Deno")

	try:
		with (root_dir / "config.json").open(encoding="utf-8-sig") as config_file:
			library_path = json.load(config_file).get("library_path")
		if not isinstance(library_path, str) or not library_path.strip():
			missing.append("music library path")
	except (OSError, json.JSONDecodeError):
		missing.append("valid config.json with a music library path")

	return missing


def _reload_user_environment():
	if os.name != "nt":
		return

	import winreg

	try:
		with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as environment_key:
			user_values = {}
			index = 0
			while True:
				try:
					name, value, _ = winreg.EnumValue(environment_key, index)
				except OSError:
					break
				user_values[name.upper()] = value
				index += 1
	except FileNotFoundError:
		return

	for name in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET"):
		if name in user_values:
			os.environ[name] = str(user_values[name])

	user_path = user_values.get("PATH")
	if user_path:
		os.environ["PATH"] = os.path.expandvars(str(user_path)) + os.pathsep + os.environ.get("PATH", "")


def ensure_requirements(root_dir):
	root_dir = Path(root_dir)
	missing = _missing_requirements(root_dir)
	if not missing:
		return

	print("Convenify setup is needed: " + ", ".join(missing))
	setup_script = root_dir / "Savify_automation" / "setup.ps1"
	result = subprocess.run(
		[
			"powershell.exe",
			"-NoProfile",
			"-ExecutionPolicy",
			"Bypass",
			"-File",
			str(setup_script),
		],
		cwd=root_dir,
	)
	if result.returncode != 0:
		raise SystemExit("Convenify setup did not complete successfully.")

	_reload_user_environment()
	missing = _missing_requirements(root_dir)
	if missing:
		raise SystemExit(
			"Setup completed, but these requirements are still unavailable: "
			+ ", ".join(missing)
			+ ". Restart your terminal and try again."
		)