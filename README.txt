FANTASY HQ - ESPN fantasy football helper

HOW TO RUN (Mac)
1. Unzip this folder somewhere you'll keep it (e.g. Documents).
2. Open the Terminal app.
3. Type  cd  followed by a space, drag the FantasyHQ folder into the Terminal window, press Return.
4. Type  python3 server.py  and press Return.
5. Open http://localhost:8765 in Chrome.
6. Go to Settings, enter your league ID, paste your espn_s2 and SWID cookies, click Connect league.

Leave the Terminal window open while you use the app. Press Ctrl+C in it to stop.
Nothing to install: it only needs the python3 that comes with macOS
(if macOS asks to install "command line developer tools", click Install).

DATA SOURCES
ESPN (your league, projections, analyst ranks, injury news), Sleeper (projections,
injuries, trending adds), CBS Sports (projections, injury report), FantasyPros
(expert consensus rankings, via the open DynastyProcess data set), FantasyCalc (trade values).

PUT IT ONLINE AS A WEBSITE (free, via Render)
1. Make a free GitHub account, create a new repository, and upload
   server.py, index.html, render.yaml and README.txt to it.
2. Make a free account at render.com and sign in with GitHub.
3. Click New > Blueprint, pick your repository, click Apply.
4. After a minute or two Render gives you a link like
   https://fantasy-hq-xxxx.onrender.com - send that to friends.
Each person enters their own league ID and ESPN cookies in Settings;
cookies stay in their own browser and are only passed through to ESPN.
The free plan sleeps when unused, so the first visit can take ~30 seconds.
