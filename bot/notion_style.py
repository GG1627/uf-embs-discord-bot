"""Shared visual language for the officer desk; artwork is optional."""

import os
import random
from pathlib import Path

import discord

ASSET_DIR = Path(__file__).resolve().parents[1] / 'assets' / 'notion'
ART = {'digest': 'cat4.jpg'}
STATUS_ART = tuple(f'cat{n}.jpg' for n in range(1, 9)
                   if n != 4 and (ASSET_DIR / f'cat{n}.jpg').is_file())
ART_FILES = frozenset((*STATUS_ART, *ART.values()))
COLORS = {'blue': 0x5865F2, 'mint': 0x2DD4BF, 'amber': 0xFBBF24, 'red': 0xFB7185}


def card(title, description=None, tone='blue', art=None):
    embed = discord.Embed(title=title, description=description, color=COLORS[tone])
    base = os.getenv('NOTION_ASSET_BASE_URL', '').rstrip('/')
    filename = random.choice(STATUS_ART) if art == 'status' and STATUS_ART and base else ART.get(art)
    if filename and base:
        url = base + '/notion/assets/' + filename
        if art == 'digest':
            embed.set_image(url=url)
        else:
            embed.set_thumbnail(url=url)
    return embed


def status_label(status):
    icons = {'waiting for room booking': '🏠', 'waiting for marketing': '📣',
             'waiting for food order': '🍕', 'marketing in progress': '🎨',
             'reimbursement needed': '🧾', 'event complete': '✅'}
    return icons.get(status.casefold(), '📝') + ' ' + discord.utils.escape_mentions(status)
