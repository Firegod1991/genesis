# A way back in: the boot stick and the spare card

A Genesis drive opens on Linux. That is fine on the Raspberry Pi it was filled for. It is a locked door on the day the Pi is gone and the only machine left is a laptop with a dead disk, or one that runs nothing but Windows.

`genesis bootstick` makes the key to that door from what the drive already carries.

## A stick that starts a PC

You need a USB stick of 8 GB or more (16 GB or more holds a second desktop system) and any Linux machine with the drive plugged in. The Pi does this fine: Ventoy's installer carries its own tools for PCs and for the Pi, and from the machine it needs only `fdisk` or `parted`, which every common Linux has.

```sh
genesis bootstick                                # lists the sticks and cards this machine sees. Writes nothing
sudo genesis bootstick --device /dev/sdX         # everything on that stick is erased
```

It installs [Ventoy](https://www.ventoy.net) on the stick, then copies the live systems from `vault/isos` onto it, most useful first, as many as fit:

1. Debian Live, a whole desktop that runs from the stick. It has Python, so Genesis starts from the drive as soon as it is up.
2. Clonezilla, GParted and Rescuezilla, to copy, repair and rescue disks.
3. Ubuntu Desktop, Linux Mint, Tails and the installers, while there is room.

To choose yourself, name them: `--iso debian-live --iso clonezilla`. Later, add a system by copying its `.iso` file onto the stick and remove one by deleting it. Ventoy needs nothing more.

On a machine that has no `mkfs.vfat`, Ventoy says so ten times and then finishes all the same. It writes that part of the stick itself, so the stick is whole.

### Starting a computer from it

1. Plug in the stick and switch the computer on.
2. As it starts, press the key for its boot menu. On most machines that is F12, F10, F9, F2 or Esc.
3. Pick the USB stick, then pick the first system on the list.
4. When the desktop is up, plug in the Genesis drive, open it in the file manager and double click **Start Genesis**.

Nothing on the computer's own disk is touched. If the stick does not show in the boot menu, switch off Secure Boot in the computer's setup screen, or enroll the Ventoy key when the stick offers to. The same steps are in `READ ME.txt` on the stick.

## A spare card for a Raspberry Pi

The card a Pi starts from wears out. This writes a new one from the drive, with no connection:

```sh
sudo genesis bootstick --pi --device /dev/sdX          # Raspberry Pi OS with the desktop
sudo genesis bootstick --pi --lite --device /dev/sdX   # the small one, with no desktop
```

The system is written to the card, then the card is read back and compared with what was written. A card that does not read back right is called bad, which is exactly what you want to learn before the day you need it. On its first start the Pi asks you to make a user, and then the drive works as before.

## What it will not do

Both commands erase the disk they are given, so nothing is written until the disk passes every check:

* It has to be a whole disk, not one part of a disk.
* It must not hold the Genesis drive, this copy of Genesis, or the system the machine is running from. No option overrides that.
* It has to be a USB stick or a card, and smaller than 300 GB. A disk that does not look like one is refused. If it really is the one to erase, `--force` says so.
* You type the name of the disk to confirm, and Ventoy then asks twice more. `--yes` answers all of it for you: use it only in a script.
* It needs root, because writing a whole disk does.

On Windows and macOS the command explains itself and stops. Use Rufus or Raspberry Pi Imager there, with the files in `vault/isos`. The dashboard lists them under Programs, so any phone or laptop joined to the box can take them.

## How far this has been tried

Both commands have been run for real against disk images on a Linux PC, with the Ventoy and the systems a filled drive carries. Ventoy 1.1.18 installed itself, a live system was copied onto the image and compared byte for byte with the one on the drive. Raspberry Pi OS was written, read back and found right, and its boot files for the Pi 5 were in place. The guard was tried with the Genesis drive plugged in: the drive was never offered, and naming it was refused, with `--force` and without.

Not yet done: a physical stick started on a real PC, and a physical card started in a real Pi. If you do either, a report is welcome.
