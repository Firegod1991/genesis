# Run it from the drive: no Docker, no internet

Genesis runs straight from the drive it fills. A Raspberry Pi 5 with the drive, a screen, a keyboard and a mouse is enough. So is any Linux PC.

## Once, while you still have a connection

```sh
genesis fetch --tier ark --platform linux-arm64,linux-x64,android
genesis ready
genesis seal        # optional: the SHA 256 of every file, so the drive can prove itself later
```

`genesis ready` does four things, all of them on the drive:

* It unpacks the Kiwix server, Ollama and 7-Zip for every machine the drive carries them for, into `vault/runtime/`.
* It writes the list of books the Kiwix server reads when it starts. Given hundreds of books by name the server opens every one before it answers, which takes minutes on a hard disk. Given the list it answers in a second.
* It draws the first page of every manual as its cover. This needs `pdftoppm`, from poppler.
* It puts **Start Genesis** and **Genesis Safe** at the top of the drive, with `READ ME FIRST.txt`, one page that says how to use the drive, and gives the folders their pictures.

Run it again after you add books. After adding to a sealed drive, `genesis seal --quick` brings the seal up to date by reading only what is new: a file whose size and time have not changed keeps the checksum it had, and a download keeps the one it was proved with. `genesis verify --sealed` still reads every file.

Two more things live on the drive beside the library: a locked safe for your own papers ([SAFE.md](SAFE.md)), and the means to make a USB stick that starts a PC or a spare card for the Pi ([BOOTSTICK.md](BOOTSTICK.md)).

## Every time after that

Double click **Start Genesis** on the drive, or run:

```sh
sh "/media/$USER/GENESIS/Start Genesis.sh"
```

That is `genesis start`. It needs Python 3 and nothing else. It starts three things and opens the browser:

| | Port | What |
|---|---|---|
| Front door | 8080 | One question box, then manuals, plants, library, map, scripture, radio, notes and programs |
| Book reader | 8081 | Kiwix, serving every book on the drive |
| Assistant | 11435, this machine only | Ollama with the models on the drive, beside any Ollama installed on the machine (11434) |

`genesis stop` stops them. Phones and laptops on the same network can open port 8080 of the box.

## On a Mac or a Windows PC

The same start works there, with the book reader and the assistant. A copy of Genesis keeps its vault in a `vault` folder inside itself, and a first start offers to fill it: a starter of about 1.1 GB, the starter with the assistant, the seed tier, or nothing yet.

* **Mac**, Intel or Apple silicon. Open **Start Genesis.command** in the Mac download, or in Terminal: `sh genesis/bin/genesis start`. The book reader is built for macOS 12 and newer, the assistant for macOS 14 and newer.
* **Windows 10 or 11.** Double click **Start Genesis** in the folder, and **Stop Genesis** to stop it. The Windows download carries Python. A copy from git needs it, from the Microsoft Store or python.org. The book reader also needs Microsoft's Visual C++ runtime, which most PCs already have. Genesis says so when it is missing.

The drive's own Ollama runs on these two as well: `genesis ready`, or the first start on that machine, unpacks the build for Windows or the one for every Mac when the drive carries it. An Ollama installed on the machine keeps running beside it, with its own models. 7-Zip is unpacked for Linux only.

A drive formatted for Linux, as a drive filled for a Raspberry Pi usually is, cannot be read by Windows or by a Mac. Start that computer from the boot stick instead ([BOOTSTICK.md](BOOTSTICK.md)).

If another program already holds port 8080, Genesis answers on the next free port (8090, 8765 or 8780) and says which.

## The assistant

It runs on the box. It searches the library first, is handed what it found, and names the book it used. The smallest everyday model is offered first because it answers soonest. Small models make mistakes: for anything that can hurt you, pick the largest model the box can hold and open the manual it points to.

## When something goes wrong

* **The drive does not show up on a Pi 5.** Use the official 27 W supply. With a weaker one the Pi limits USB power and a hard disk may not spin up.
* **Notes will not save.** The drive belongs to another user. Run once: `sudo chown -R $USER "/media/$USER/GENESIS"`
* **The reader takes minutes to start.** The book list is out of date. Run `genesis ready`.
* **The double click does nothing.** Use the terminal line above.
