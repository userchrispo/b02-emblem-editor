# Using the BO2 Emblem Toolkit

This assumes you've already finished the one-time [install](INSTALL.md) and have the control panel open in your browser.

## The three modes

| Mode | What it does |
|---|---|
| Off | Normal internet. Nothing is captured or changed. |
| Capture | Saves the emblem of any player whose profile or channel you open. |
| Show | Serves your selected emblem to the PS5 *while the proxy is on*. That is a preview until you save it in the in-game editor. |

Only one mode runs at a time.

## Capturing an emblem

1. Click Capture.
2. On your PS5, open the profile or channel of the player you want to copy from.
3. Their emblem shows up under **Your emblems** within a few seconds. Nothing else to do.
4. A player can have more than one saved emblem. If so, each one shows up as its own card.

Click the text under a card to rename it to something you'll recognize later, like "Sam's dragon emblem," instead of the default.

The toolkit can keep many captured emblems on your PC. Show mode only sends **one** of them to the console at a time (the card with the checkmark). Search the list by name, **Import .bin** to add a file you already have, and **Delete** on a card to throw one away.

The header **PS5 last seen** line updates when the console actually requests emblem data through this proxy. If it stays on “no emblem request yet,” the PS5 is not using the proxy setting.

After a successful Show load, a banner stays up until you click **I restarted the game**. That is the reminder that BO2 will not fetch a second emblem until you restart (or Zombies → Multiplayer).

## Copying an emblem onto your own account

Seeing it on your player card is **not** the same as owning it. While Show is on, the proxy can paint that emblem onto your card and into the editor. The moment you disconnect the proxy or switch to Off, the console goes back to whatever is actually stored on your account. If you never saved in the in-game editor, it will be gone.

1. Click the emblem card you want. A checkmark shows which one is selected.
2. Click **Show**.
3. On the PS5, open **your own** emblem editor, not another player's profile.
4. Pick the slot you want this copy to live in (an empty slot, or one you are fine overwriting).
5. **Save in the game**, the same way you save an emblem you painted yourself. That upload is what writes it to your account.
6. To prove it stuck: switch the toolkit to **Off** (or take the proxy off the PS5) and look at that slot / your player card again. If it is still there, it is yours.

## Copying a second (or third) emblem

Black Ops II only fetches emblem data **once per game session**. After you open the editor the first time, it keeps that result. Selecting a different card in the control panel will not change what the console already cached. Backing out of the editor and going back in is not enough.

Do this for each extra copy:

1. Finish saving the previous emblem in-game first (step 5 above).
2. Restart Black Ops II, **or** switch to Zombies and back to Multiplayer, so the editor cache clears.
3. In the control panel, click the next emblem, then **Show**.
4. Open your emblem editor again, pick a **different** slot from the one you already saved, and save.
5. Repeat from step 2 if you want another one.

If you skip the restart/mode switch, the second copy never loads and it will look like “the other one is gone.” If you save again into the same slot, you overwrite the first copy on your account.

## Building an emblem on your PC

The in-game editor is slow. This toolkit includes a PC editor at
`http://localhost:8090/editor.html` (there's a link in the control panel).

There is **no From image**. Dropping a picture and rebuilding it with 32 shapes was removed because the result was not a usable copy.

1. Click **Emblem editor**, or **New emblem**.
2. Click shapes on the left to add layers. Drag on the canvas to move them. Use the right-hand panel for color, size, rotation, outline, and flip. Ctrl+Z undoes, Ctrl+D duplicates a layer, Ctrl+C / Ctrl+V copy and paste. Outlined layers show as a thin edge on the live canvas.
3. Drag layers in the list to change order. The top of the list is the front.
4. **Save** keeps a copy in "Your emblems" on the PC. **Use in Show** saves it, selects it, and switches the proxy to Show mode.
5. Open your own emblem editor on the PS5 and **save in the game**, same as with a captured emblem. Until you do that, it will vanish when you leave the proxy.

You can also click **Edit** on any captured emblem to load it into the PC editor. Saving a capture creates a new copy; it doesn't overwrite the original. **Download .bin** exports the same file format the toolkit uses for Show mode.

The live canvas is a working preview. Outlined layers look filled while you edit; the game (and the thumbnail after you save) draws the real outline.

## Two things to know before you start

**The editor only checks the server once per game session.** The first time you open your emblem editor, Black Ops II caches whatever it loads and won't ask again after that, even if you pick a different emblem in the control panel. If you need to load a second or third emblem, you have to clear that cache first: either fully restart the game, or switch to Zombies and back to Multiplayer, then open the editor again. Just backing out of the editor and going back in isn't enough.

**It won't work if the emblem uses a shape you haven't unlocked.** Emblems are built from shapes, ranks, and weapon-qualification icons that Black Ops II normally only lets you use once you've earned them. If a captured emblem includes something your own account hasn't unlocked, the game may fail to load it, show it incorrectly, or refuse to save it. There's no way around this from the toolkit's side, since the game itself is enforcing it, not the proxy.

## Questions

**Do I need to keep the terminal window open?**
Yes. Closing it stops the proxy and the control panel. You can minimize it.

**Does this affect signing in or matchmaking?**
No. Only the plain-HTTP emblem-storage requests are touched. Everything else, including all HTTPS traffic like PSN sign-in, passes through unmodified and is never decrypted.

**Can I run this for more than one PS5?**
Yes. The proxy setting is per-console, so any number of PS5s on your network can point at the same running toolkit.

**I copied one emblem, then tried another and the new one never showed / the first one vanished.**
The game still has the first load cached, so it never asked the proxy for the second emblem. Save the first one in the in-game editor, restart the game (or Zombies → Multiplayer), select the second card, turn Show on, open the editor, and save it to a **different** slot.

**I disconnected from the proxy and the emblem left my account. It only stayed on my player card while I was connected.**
Show mode was only *displaying* it. The player card can show the injected emblem for as long as the PS5 is using the proxy. It is not written to your account until you open **your** emblem editor and save. After that, turn the toolkit Off and check again — that is the real test.

**The control panel never leaves “no emblem request yet”**
The PS5 is not sending emblem HTTP through this PC. Recheck the proxy IP/port, firewall, and that the toolkit window is still open. **PS5 last seen** only moves when the game actually asks for emblem data.

**I switched back to Off but I still see the captured emblem somewhere. Why?**
That's the PS5's own local cache still showing what it last loaded, not something the toolkit is still doing. Switching modes only changes what the proxy would serve on the next request, it doesn't force the console to ask again.
