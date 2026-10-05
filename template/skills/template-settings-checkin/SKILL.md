---
name: template-settings-checkin
description: >-
  Use in any shareable bot template: at the end of first-run setup to create the
  settings check-in routine, and whenever that routine fires to remind the owner
  of their settings and reschedule the next check-in 2–6 weeks out.
---
# Settings check-in

A light, recurring reminder of what the owner set up, so settings don't go stale and they remember they can change them.

## At the end of first-run setup

1. Check your routines for an existing "Settings check-in". If there is one, keep it and don't create a second.
2. Otherwise create a routine named "Settings check-in" scheduled ONCE, 14 days from today, at a reasonable daytime hour in the owner's timezone (use the timezone you confirmed during setup, never an assumed one). Pick an off-the-hour minute. Its prompt: "Run the template-settings-checkin skill (When it fires)."
3. Tell the owner in one line: "In two weeks I'll show you your settings once so you can tweak anything; you pick how often after that." It can be enabled with the other routines once they confirm.

## When it fires

1. If the owner changed settings in the last 7 days, or setup isn't finished, don't message. Reschedule 14 days out and stop.
2. Otherwise send ONE short message:
   - A one-line opener, e.g. "Quick settings check. Here's how I'm set up for you:"
   - The current settings as a short list, read from your memory and routines: what you watch, connected accounts, routine names and times, thresholds, quiet hours, and opt-in extras on or off. Plain words, no internal ids, no secrets.
   - One line: "Want to change anything?"
   - A question widget: "When should I check in next?" with options 2 weeks, 3 weeks, 4 weeks, and 6 weeks (allowCustom for any number of weeks from 2 to 6).
3. When they answer, apply any setting changes they ask for and confirm in one line.
4. Reschedule this same routine (update, don't create) to fire ONCE on the chosen date, at the same hour. If they don't answer within a few days, or dismiss it, reschedule 4 weeks out. Clamp any answer to 2–6 weeks.
5. Save the chosen interval as a memory and use it as the suggested option next time.

## Rules

- Never more than one check-in message per firing, and never alongside another routine's digest if you can avoid it.
- Never change a setting without the owner asking.
- If the owner says to stop check-ins, pause the routine and say how to turn it back on.
