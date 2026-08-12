<div align="center" style="background-color:#0d1117; padding:32px; border-radius:12px;">

  <img src="./GithubBanner.png" alt="Github banner" width="1080" height="383" />
  <h2 style="color:#f0f6fc;">A python-based music downloader, designed for conveniently syncing your local library with your Spotify playlists.</h2>
</div>

<h3>Description</h3>
<p>
  This program is a custom wrapper for LaurenceRawlings/savify that i made for myself, in order to make it as simple as possible to sync my local music with my Spotify library. </br>
  There are 2 scripts included, a manual downloader (download.py) and an automated library updater (update_library.py). The manual downloader takes a link (or a file containing multiple links) and sorts the downloaded tracks into a specific folder structure depending on whether it's a track, playlist or album. The updater takes an internal list of playlists, each entry consisting of name, path, link and genre of the playlist. When run, it iterates through these playlists, downloads any newly added tracks, puts them into the specified directory and then adds a genre tag to each downloaded file, making it especially convenient for DJs who like to have all music of a genre easily accessible in one place. </br> 
  The first version of this used the .exe release of Savify, and the basic mechanism is still based on that principle: </br>
  Convenify takes a Spotify link, looks into the URL, and then builds a custom command to launch Savify with certain parameters. Savify takes the metadata from Spotify, searches for the track on youtube, and then downloads it in the highest quality available using yt-dlp. </br>
  You can edit config.json and playlists.json to fit your local archive, specifying locations, Savify parameters and your own playlists to be synced by update_library.py. </br> </br>
</p>

<h3>Installation</h3>
<p>
To use the Savify Python module you will need your own Spotify developer application to access their API. </br>
To do this sign up here: https://developer.spotify.com</br>
When you have made a new application take note of your client id and secret. Now you need to add 2 environment variables to your system:</br>
<b>SPOTIPY_CLIENT_ID</br>
SPOTIPY_CLIENT_SECRET</b> </br>
To find out how to do this find a tutorial online for your specific operating system.</p>

<h3>Usage</h3>
<p>
On Windows, you just need to set your library path in config.json and start either script with the included .bat launchers. For update_library.bat, you also need to specify playlists to update in playlists.json.
</p>

<h3>Known Issues</h3>
<p>
  <b>There is one major issue that i wasn't able to fix yet:</b> </br>
  After downloading ~50 tracks, Youtube starts rejecting all HTTP requests, returning 403: Forbidden. Presumably this is some kind of DDoS protection. I've tried multiple suggested fixes from the issue page of yt-dlp, but to this point none of them really worked. Any help you could provide to fix this would be massively appreciated. </br>
  <b>And one issue that might not be fixable at all:</b> </br>
  In some cases, approximately 1% of downloaded tracks, Savify picks some random ass video to download audio from when it can't find the actual track. It will still label it as though it was the right track though, so you might only notice when loading it in your music player or DJ software. I don't think I can fix this, as it's an issue with Savify itself. A lot of the time when it happens, looking for the broken track on Youtube myself i can find and download it manually without issue. It doesn't happen often though, just make sure you have the right track loaded before transitioning your DnB track into some random makeup tutorial.
</p>
