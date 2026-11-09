<div align="center" style="background-color:#0d1117; padding:32px; border-radius:12px;">

  <img src="./GithubBanner.png" alt="Github banner" width="1080" height="383" />
  <h2 style="color:#f0f6fc;">A python-based music downloader designed for quality and convenience.</h2>
</div>

<h3>Description</h3>
<p>
  This program is a custom upgrade of LaurenceRawlings/savify that i made for myself, originally as just a small wrapper, in order to make it as simple as possible to sync my local music with my Spotify library. </br>
  There are 2 main scripts; a manual downloader (download.py) and an automated library updater (update_library.py). The manual downloader takes a link (or a file containing multiple links) and sorts the downloaded tracks into a specific folder structure depending on whether it's a track, playlist or album. The updater takes an internal list of playlists, each entry consisting of name, path, link and genre of the playlist. When run, it iterates through these playlists, downloads any newly added tracks, puts them into the specified directory and then adds a genre tag to each downloaded file, making it especially convenient for DJs who like to have all music of a genre easily accessible in one place. </br> 
  If the manual downloader is provided with a .txt file (you can also just type "q" to make it use download-queue.txt), it iterates through all links in the file. After it has processed all of them, you'll be shown a report listing the successful downloads and the songs that failed along with their line in the text file and error message. </br>
  You can edit config.json and playlists.json to fit your local archive, specifying locations, Savify parameters and your own playlists to be synced by update_library.py. </br> </br>
  The first version of this used the .exe release of Savify, and the basic mechanism is still based on that principle: </br>
  Convenify takes a Spotify link, looks into the URL, and then builds a custom command to launch Savify with certain parameters. Savify takes the metadata from Spotify, searches for the track on youtube, and then downloads it in the highest quality available using yt-dlp. </br> </br>
  With version 2.0, there is also a expansive audit and repair function included, which you can run on your existing collection to verify each track and re-download & tag those that are broken. This is because earlier versions used to occasionally download the wrong track, so after fixing this issue with v2.0, I needed to find those outliers among my ~16.000 track library. In my case, it found 2.550 incorrect tracks, which it replaced with their correct versions. You can launch the repair feature with my preconfigured options using the respective .bat launcher. </br>
  The library repair function also comes with a handy Traktor Pro 3 implementation, which you can run optionally after the library repair was completed. It takes a collection.nml path, reading the run's log and removing any repaired tracks from the collection, so that they can be cleanly reimported afterwards. There's also the "NML-Mode" (Repair-Library-NML), which you can use to manually process a .nml file containing broken tracks, force-redownloading them and then also removing them from your collection.nml afterwards.
</p>

<h3>Installation</h3>
<p>
To use Convenify, create a Spotify developer application at https://developer.spotify.com and note its client ID and client secret. If either downloader is started before setup is complete, it launches <code>Savify_automation/setup.ps1</code> and prompts for those values and your music library folder path. The script installs Deno using winget if it is not already installed, saves the Spotify credentials as user environment variables, and writes the library path to <code>config.json</code>. You can also run the PowerShell script directly. No administrator access is required.
</br>
</p>

<h3>Usage</h3>
<p>
On Windows, start either script with the included .bat launchers. For update_library.bat, you also need to specify playlists to update in playlists.json.
</p>

<h4>Genre tagging with download.py</h4>
<p>
  The manual downloader tags only the MP3 files created by each download. For a pasted link, append an argument such as <code>genre:"Drum and Bass"</code>. In a queue file, an argument-only line applies to the consecutive links below it until a blank line or comment; an argument appended to an individual link overrides the block, including with <code>genre:""</code> to skip tagging that link.
</p>
<pre>
genre:"Drum and Bass"
https://open.spotify.com/track/example
https://open.spotify.com/album/example genre:"Liquid"
https://open.spotify.com/track/example genre:""
</pre>

<h3>Known Issues</h3>
<p>
  <b>There's currently no fully working version for Linux</b> </br>
  I've started implementing pathlib to uncouple Convenify from Windows, but currently everything is fine-tuned for Windows. I'm not a Linux desktop user so if anyone wants to help out with that, feel free to contribute.</br>
  <b>Occasional HTTP errors:</b> </br>
  After downloading for a while, Youtube starts rejecting HTTP requests, returning 403: Forbidden. Presumably this is some kind of DDoS protection. I've modified Savify to include long pauses between downloads and only retry downloading once, and that seems to have improved it a lot. Updating yt-dlp regularly also helps. I've introduces a final batch report so you can easily tell what to remove from the queue. </br>
  </br>
  <b>The big issue that was fixed with v2.0:</b> </br>
  In some cases, the Savify module that Convenify is based on didn't select the right track to download. Instead, it would download too short tracks, whole albums, other more popular songs, or just random videos. Since v2.0, there is now an extensive selection & checking mechanism which uses the extracted metadata to determine accuracy values, rank multiple results accordingly based on them, and pick the most likely match if a certain threshold is met. This should, hopefully eliminate the issue of wrong tracks being downloaded. Please open an issue if you still observe strange behavior.
</p>
