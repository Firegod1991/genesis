# The safe: your own papers on the drive

A library tells you how to purify water. It does not prove who you are, who owns the house or what your child is allergic to. The safe is one locked place on the drive for the papers only your family has: identity papers, deeds and titles, medical records, insurance, account numbers, and pictures of what cannot be replaced.

## Using it

`genesis ready` puts a **Genesis Safe** icon at the top of the drive, beside Start Genesis.

1. Double click **Genesis Safe**. The first time, an empty folder opens. Put your papers in it.
2. Double click **Genesis Safe** again and it offers to lock the safe. The first time you choose the passphrase, typed twice.
3. From then on the same icon asks for the passphrase and opens the safe, and locks it again when you are done.

In a terminal it is the same thing:

```sh
genesis safe             # open it, or lock it when it is open
genesis safe open
genesis safe lock
genesis safe status
genesis safe lock --new-passphrase      # asks for the old passphrase first, then the new one twice
```

## What happens to your papers

* **Locked**, they are one file on the drive: `vault/files/safe/safe.7z`. It is an ordinary 7-Zip archive, encrypted with AES 256, and the names of the files inside are hidden with the rest. Without the passphrase it shows nothing.
* **Open**, they are an ordinary folder. On Linux, and so on a Raspberry Pi, that folder lives in the memory of the machine and never on a disk. Use it like any folder: add, change, delete.
* **Locking** packs the folder into a new archive, opens that archive again to prove it is whole, puts it in place of the old one, and wipes the folder. The safe as it was before is kept beside it as `safe.7z.bak`, one step back, in case a lock ever goes wrong.

Genesis asks for the passphrase and hands it to 7-Zip directly. It is never stored, never shown, and never put where another program could read it. The page in the dashboard only says whether a safe exists: the safe is never opened through the browser, so the passphrase is typed on the box and nowhere else.

## The passphrase

* Nobody can recover it. Not Genesis, not the people who wrote 7-Zip. A forgotten passphrase means the papers are gone.
* Use a few words that do not belong together, eight characters at the very least. A short one can be guessed by a machine that tries millions.
* Write it down and keep it somewhere that is not this drive.
* Letters beyond the English alphabet work, and the passphrase is the same one on a Raspberry Pi, on a PC and in the 7-Zip window. Make sure every keyboard you may have to use can type it.
* Locking always asks for the passphrase the safe already has, so a slip of the fingers can never lock you out under a passphrase you did not mean.

## Without Genesis

The safe does not depend on Genesis. Any computer with 7-Zip opens `safe.7z` with the same passphrase: on Linux `7zz x safe.7z`, on Windows and macOS through the 7-Zip window. The drive carries 7-Zip for Linux in `vault/tools`, and `genesis ready` unpacks it into `vault/runtime` so it is there with no connection.

Because it is encrypted, the file is safe to copy. Keep a second copy on another stick or another drive: one drive is one accident away from none.

## What to know before you trust it

* **Lock it before you switch off.** The open folder is in memory. If the power goes while the safe is open, what you changed since you opened it is lost. What was locked before is still there.
* **Memory can spill to disk.** A machine that is short of memory may write parts of it to its swap space. On a machine you do not control, do not open the safe.
* **Windows and macOS** have no folder in memory to offer, so there the open folder is on a disk. Genesis writes over its files when it locks, which helps on a hard disk and cannot be promised on flash storage. On those systems `genesis safe` needs 7-Zip installed.
* **The old p7zip is not used.** Some Linux systems still install it under the name `7z`. It cannot be handed a passphrase the way the safe needs, so Genesis passes it over and uses the 7-Zip the drive carries. With neither, install the package called `7zip`.
* **Very large safes.** The open folder has to fit in memory. It is made for papers and a few pictures, not for a film collection. To open it somewhere else, name a folder: `GENESIS_SAFE_OPEN=/some/folder genesis safe open`.
* **The seal leaves it out.** `genesis seal` records every file Genesis put on the drive. The safe and your notes change whenever you use them, so they are not part of it.

## How far this has been tried

Every promise on this page is a test in `tests/test_safe.py`. The tests run against a stand in on every machine, and against the real 7-Zip wherever a machine has one: so far 7-Zip 26.06 on Linux and on Windows. Not yet tried: a Raspberry Pi itself, and macOS.
