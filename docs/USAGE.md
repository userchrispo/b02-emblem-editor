# Using the BO2 Emblem Toolkit

This assumes you've already finished the one-time [install](INSTALL.md) and have the control panel open in your browser.

## The three modes

| Mode | What it does |
|---|---|
| Off | Normal internet. Nothing is captured or changed. |
| Capture | Saves the emblem of any player whose profile or channel you open. |
| Show | Loads your selected emblem into your own emblem editor. |

Only one mode runs at a time.

## Capturing an emblem

1. Click Capture.
2. On your PS5, open the profile or channel of the player you want to copy from.
3. Their emblem shows up under "Your captured emblems" within a few seconds. Nothing else to do.
4. A player can have more than one saved emblem. If so, each one shows up as its own card.

Click the text under a card to rename it to something you'll recognize later, like "Sam's dragon emblem," instead of the default.

## Copying an emblem onto your own account

1. Click the emblem card you want. A checkmark shows which one is selected. Only one can be selected at a time.
2. Click Show.
3. Open your own emblem editor on the PS5, not another player's profile. The captured emblem loads there instead of your usual saved emblem.
4. Save it from the editor, the same way you'd save anything you built yourself. It's now permanently on your account.

You can change your selection at any point, in any mode. It takes effect as soon as you're in Show mode and reopen the editor.

## Building an emblem on your PC

The in-game editor is slow. This toolkit includes a PC editor at
`http://localhost:8090/editor.html` (there's a link in the control panel).

1. Click **Emblem editor**, or **New emblem**.
2. Click shapes on the left to add layers (32 max, same as the game). Drag on the canvas to move them. Use the right-hand panel for color, size, rotation, outline, and flip.
3. Drag layers in the list to change order. The top of the list is the front.
4. **Save** keeps a copy in "Your emblems." **Use in Show** saves it, selects it, and switches the proxy to Show mode.
5. Open your own emblem editor on the PS5 and save, same as with a captured emblem.

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

**I switched back to Off but I still see the captured emblem somewhere. Why?**
That's the PS5's own local cache still showing what it last loaded, not something the toolkit is still doing. Switching modes only changes what the proxy would serve on the next request, it doesn't force the console to ask again.
