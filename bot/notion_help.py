"""Plain-text help for everyday officer use."""

NOTION_INTRO = """**Welcome to the Notion channel! 🐈**

This channel connects to our Notion workspace so we can keep up with event plans here.

• Status changes in Notion get posted automatically.
• Check upcoming events, rooms, and deadlines — or update a plan with `!notionedit`.
• The weekly digest lands Sunday at 10 AM Eastern.

Try `!notion week` to see what's coming up. Need a hand? Run `!help` for commands and examples."""

NOTION_HELP = """**Notion commands**
Check event plans and make updates without leaving Discord.

**Most used**

`!notion week`
Shows this week's events, statuses, dates, rooms, and Notion links.
You can also use `!notion today`, `!notion nextweek`, or `!notion month`.
Just `!notion` shows this week and next week.

`!notionedit Intro to Git`
Choose a field, see its current value, and pick an option or fill in a form. Review the change, then Confirm or Cancel. Use `!notionedit` alone to pick an event first.

`!ask What events are happening next week?`
Ask like you're chatting with Claude. It can find events by date and status, then show the matching plans. This doesn't change anything.

`!notionupdate Set Intro to Git location to Room 201`
Tell it what you want changed. You'll see the current and proposed values before confirming. One field at a time.

**Useful extras**

`!notion event Intro to Git`
Shows the full event details, including description, priority, and post deadline.

`!notion missingrooms`
Lists events in the next 30 days that still need a room.

`!notion deadlines`
Shows overdue post deadlines and those coming up in the next seven days.

`!notion reimbursements`
Lists events marked Reimbursement Needed.

`!notion week Waiting for Marketing`
Filters the event list by status. Use `!notionstatuses` to see the available statuses.

`!notion digest`
Previews next week's summary without pinging anyone.

All times are Eastern. Type `!help` to see this again."""
