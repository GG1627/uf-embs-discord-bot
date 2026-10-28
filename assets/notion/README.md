# Bot artwork

Cats appear on Notion status-change notifications, weekly digests, and the channel introduction.

- Status changes: randomly choose an existing `cat1.jpg` through `cat8.jpg`, excluding `cat4.jpg`, as a top-right thumbnail. Missing files are skipped (cat2 is currently absent).
- Weekly digest and digest previews: `cat4.jpg` is a large image below the content.
- `!intro`: `cat1.jpg` is attached to the plain-text channel welcome.
- Event lists, planning checks, empty results, help, editors, confirmations, and errors have no images.

Set `NOTION_ASSET_BASE_URL` to the bot origin to show images, or leave it blank to hide them.
The asset endpoint serves only the approved cat JPG filenames. No image API calls run during bot use.
After adding or replacing files, redeploy; Discord may cache existing image URLs.

The original generated artwork is no longer used. Its generation prompts remain below for reference.

## Original generation prompts

### help

Square editorial photograph for a tiny Discord bot thumbnail: an extremely serious orange tabby cat sitting upright at a desk with a small spiral notebook and one oversized blue pencil, giving the camera a hilariously judgmental 'I have a plan' stare. Charming internet cat meme energy, realistic expressive cat, warm cream background, subtle cobalt blue accents, soft natural light, crisp simple silhouette legible at 96 pixels. One cat, uncluttered close crop, no words, no letters, no watermarks, no human hands. Asset is for a student club event planning help guide.

### digest

Square editorial photograph for a tiny Discord bot thumbnail: an orange tabby cat solemnly reading a tiny blank folded newspaper beside a small coffee cup, comically concerned expression like a busy event-planning manager on Sunday morning. Charming internet cat meme energy, realistic expressive cat, warm cream background, cobalt blue accent, soft natural light, close simple composition legible at 96 pixels. One cat, no readable text, no letters, no watermarks, no human hands. Coordinate with a cream and blue orange-cat club-planning mascot.

### empty

Square editorial photograph for a tiny Discord bot thumbnail: a smug orange tabby cat asleep with its chin on a closed blue notebook, paws tucked under, the unmistakable relaxed face of 'nothing left to do'. Charming funny internet cat meme energy, realistic expressive cat, warm cream backdrop and cobalt blue notebook, soft natural light, uncluttered close crop legible at 96 pixels. One cat, no words, no letters, no watermarks, no human hands. Coordinate with a cream and blue orange-cat event-planning mascot.
