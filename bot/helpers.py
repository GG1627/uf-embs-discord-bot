"""Helper utility functions for profanity filtering, spam detection, and role management."""

import re
import discord
from words.BANNED_WORDS import bad_words
from words.ALLOWED_WORDS import chill_profane_words
from words.SPAM_WORDS import spam_words
from bot.config import UNVERIFIED_ROLE_NAME, MEMBER_ROLE_NAME


def contains_allowed_words(text: str) -> bool:
    """Check if message contains any allowed profane words."""
    text_lower = text.lower()
    # Check each allowed word (with word boundaries to avoid false positives)
    for word in chill_profane_words:
        # Use word boundaries to match whole words only
        pattern = r'\b' + re.escape(word.lower()) + r'\b'
        if re.search(pattern, text_lower):
            return True
    return False


def contains_banned_words(text: str) -> bool:
    """
    Check if message contains any banned slurs/hate speech.
    Allows longer words that contain banned words (e.g., "class" containing "ass").
    """
    text_lower = text.lower()
    
    for banned_word in bad_words:
        banned_word_lower = banned_word.lower()
        
        # Find all occurrences of the banned word
        for match in re.finditer(re.escape(banned_word_lower), text_lower):
            start_pos = match.start()
            end_pos = match.end()
            
            # Check if it's at a word boundary (standalone word)
            # Word boundary means: start of string OR non-alphanumeric before, AND end of string OR non-alphanumeric after
            is_word_start = (start_pos == 0 or not text_lower[start_pos - 1].isalnum())
            is_word_end = (end_pos == len(text_lower) or not text_lower[end_pos].isalnum())
            
            # Only ban if it's a standalone word (at word boundaries)
            # If it's part of a longer word (has alphanumeric chars before/after), allow it
            if is_word_start and is_word_end:
                return True
    
    return False


def check_profanity(text: str) -> tuple[bool, str]:
    """
    Check if message contains profanity.
    Returns: (is_banned, reason)
    - If contains allowed words, return (False, "allowed")
    - If contains banned words, return (True, "banned_word")
    """
    # First check: if message contains allowed words, it's fine
    if contains_allowed_words(text):
        return False, "allowed"
    
    # Second check: if message contains banned words from our list
    if contains_banned_words(text):
        return True, "banned_word"
    
    return False, "clean"


def check_spam(text: str) -> bool:
    """
    Check if message contains at least 2/3 of the spam words.
    Returns True if message is spam, False otherwise.
    """
    if not text:
        return False
    
    text_lower = text.lower()
    matched_words = 0
    total_spam_words = len(spam_words)
    
    # Check how many spam words/phrases are present in the message
    for spam_word in spam_words:
        # Use case-insensitive search for phrases (some may be multi-word)
        if spam_word.lower() in text_lower:
            matched_words += 1
    
    # Check if message contains at least 2/3 of spam words
    # Round up: (2 * total_spam_words + 2) // 3
    # Example: 16 words → 11 needed, 9 words → 6 needed
    threshold = (2 * total_spam_words + 2) // 3
    
    return matched_words >= threshold


def get_roles(guild: discord.Guild):
    """Helper function to get Unverified and Member roles"""
    unverified = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)
    member = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
    return unverified, member

