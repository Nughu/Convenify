<div align="center" style="background-color:#0d1117; padding:32px; border-radius:12px;">

  <img src="./GithubBanner.png" alt="Github banner" width="1080" height="383" />
  <h2 style="color:#f0f6fc;">A python-based music downloader designed for quality and convenience.</h2>
</div>

<h3>Description</h3>
<p>
  This program is a custom expansion of LaurenceRawlings/savify that i made for myself, originally as just a small wrapper, in order to make it as simple as possible to sync my local music with my Spotify library. </br>
  There are 2 scripts included, a manual downloader (download.py) and an automated library updater (update_library.py). The manual downloader takes a link (or a file containing multiple links) and sorts the downloaded tracks into a specific folder structure depending on whether it's a track, playlist or album. The updater takes an internal list of playlists, each entry consisting of name, path, link and genre of the playlist. When run, it iterates through these playlists, downloads any newly added tracks, puts them into the specified directory and then adds a genre tag to each downloaded file, making it especially convenient for DJs who like to have all music of a genre easily accessible in one place. </br> 
  If the manual downloader is provided with a .txt file (you can also just type "q" to make it use download-queue.txt), it iterates through all links in the file. After it has processed all of them, you'll be shown a report listing the successful downloads and the songs that failed along with their line in the text file and error message. </br>
  You can edit config.json and playlists.json to fit your local archive, specifying locations, Savify parameters and your own playlists to be synced by update_library.py. </br> </br>
  The first version of this used the .exe release of Savify, and the basic mechanism is still based on that principle: </br>
  Convenify takes a Spotify link, looks into the URL, and then builds a custom command to launch Savify with certain parameters. Savify takes the metadata from Spotify, searches for the track on youtube, and then downloads it in the highest quality available using yt-dlp. </br>
  
</p>

<h3>Installation</h3>
<p>
To use Convenify, create a Spotify developer application at https://developer.spotify.com and note its client ID and client secret. If either downloader is started before setup is complete, it launches <code>Savify_automation/setup.ps1</code> and prompts for those values and your music library folder path. The script installs Deno using winget if it is not already installed, saves the Spotify credentials as user environment variables, and writes the library path to <code>config.json</code>. You can also run the PowerShell script directly. No administrator access is required.
</br></br>
Restart open terminals or VS Code windows after setup if it cannot detect the newly installed Deno executable.
</p>

<h3>Usage</h3>
<p>
On Windows, start either script with the included .bat launchers. For update_library.bat, you also need to specify playlists to update in playlists.json.
</p>

<h4>Genre tagging with download.py</h4>
<p>
  The manual downloader can tag only the MP3 files created by each download. For a pasted link, append an argument such as <code>genre:"Drum and Bass"</code>. In a queue file, an argument-only line applies to the consecutive links below it until a blank line or comment; an argument appended to an individual link overrides the block, including with <code>genre:""</code> to skip tagging that link.
</p>
<pre>
genre:"Drum and Bass"
https://open.spotify.com/track/example
https://open.spotify.com/album/example genre:"Liquid"

https://open.spotify.com/track/example genre:""
</pre>

<h3>Known Issues</h3>
<p>
  <b>One major issue that i wasn't able to fix yet:</b> </br>
  After downloading for a while, Youtube starts rejecting HTTP requests, returning 403: Forbidden. Presumably this is some kind of DDoS protection. I've modified Savify to include long pauses between downloads and only retry downloading once, and that seems to have improved it a lot. Updating yt-dlp regularly also helps. I've introduces a final batch report so you can easily tell what to remove from the queue. </br>
  <b>There's currently no fully working version for Linux</b> </br>
  I've started implementing pathlib to uncouple Convenify from Windows, but i'm not a Linux desktop user so if anyone wants to help out with that, feel free to contribute.</br>
  <b>And one issue that might not be fixable at all:</b> </br>
  In some cases, Savify picks the wrong track to download since it relies on searching YouTube. Often it downloads another track from the same album, the same track for 2 different versions, in some cases the whole album as one track, and sometimes even some other random ass video. It will still label it as though it was the right track though, so you might only notice when loading it in your music player or DJ software. When you collect music in mass amounts, the quota of broken tracks is negligible, but look out for tracks that sre too short, too long or have a weird waveform. I don't think I can fix this, as it's an issue with Savify itself. A lot of the time when it happens, looking for the broken track on Youtube myself i can find and download it manually without issue. It doesn't happen often though, just make sure you have the right track loaded before transitioning your DnB track into some random makeup tutorial.
</p>
