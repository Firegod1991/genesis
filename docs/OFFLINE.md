# Run it from the drive: no Docker, no internet

Genesis runs straight from the drive it fills. A Raspberry Pi 5 with the drive, a screen, a keyboard and a mouse is enough. So is any Linux PC.

## Once, while you still have a connection

```sh
genesis fetch --tier ark --platform linux-arm64,linux-x64,android
genesis ready
genesis seal        # optional: the SHA 256 of every file, so the drive can prove itself later
```

`genesis ready` does four things, all of them on the drive:

* It unpacks the Kiwix server and Ollama for every machine the drive carries them for, into `vault/runtime/`.
* It writes the list of books the Kiwix server reads when it starts. Given hundreds of books by name the server opens every one before it answers, which takes minutes on a hard disk. Given the list it answers in a second.
* It draws the first page of every manual as its cover. This needs `pdftoppm`, from poppler.
* It puts **Start Genesis** at the top of the drive and gives the folders their pictures.

Run it again after you add books.

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
| Assistant | 11434, this machine only | Ollama with the models on the drive |

`genesis stop` stops them. Phones and laptops on the same network can open port 8080 of the box.

## The assistant

It runs on the box. It searches the library first, is handed what it found, and names the book it used. The smallest everyday model is offered first because it answers soonest. Small models make mistakes: for anything that can hurt you, pick the largest model the box can hold and open the manual it points to.

## When something goes wrong

* **The drive does not show up on a Pi 5.** Use the official 27 W supply. With a weaker one the Pi limits USB power and a hard disk may not spin up.
* **Notes will not save.** The drive belongs to another user. Run once: `sudo chown -R $USER "/media/$USER/GENESIS"`
* **The reader takes minutes to start.** The book list is out of date. Run `genesis ready`.
* **The double click does nothing.** Use the terminal line above.
