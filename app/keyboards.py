from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

def main_keyboard(updates: str = "") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔎 Search Anime", callback_data="search")], [InlineKeyboardButton(text="📚 Browse Anime", callback_data="browse")], [InlineKeyboardButton(text="❓ Help", callback_data="help")]]
    if updates:
        rows.append([InlineKeyboardButton(text="📢 Updates Channel", url=updates)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]])

def admin_keyboard() -> InlineKeyboardMarkup:
    items = [("📊 Statistics","admin:stats"),("➕ Add Anime","admin:add"),("✏️ Edit Anime","admin:edit"),("🗑️ Delete Anime","admin:delete"),("🔗 Manage Links","admin:links"),("📝 Manage Posts","admin:posts"),("🖼️ Manage Posters","admin:posters"),("🔤 Manage Aliases","admin:aliases"),("📢 Force Sub Settings","admin:forcesub"),("👥 Manage Admins","admin:admins"),("👤 Searching Users","admin:users"),("🔎 Search User by ID","admin:userbyid"),("🕒 Recent Searches","admin:recent"),("✏️ Welcome Message","admin:welcome"),("🖼️ Start Image","admin:startimage"),("📦 Database Backup","admin:backup"),("♻️ Database Restore","admin:restore"),("⚙️ Bot Settings","admin:settings"),("❌ Close Panel","admin:close")]
    rows=[]
    for i in range(0,len(items),2): rows.append([InlineKeyboardButton(text=items[i][0],callback_data=items[i][1]), *([InlineKeyboardButton(text=items[i+1][0],callback_data=items[i+1][1])] if i+1<len(items) else [])])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def manage_posts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼️ Set Landscape Poster", callback_data="posts:set")],
        [InlineKeyboardButton(text="👁️ Preview Current Poster", callback_data="posts:preview")],
        [InlineKeyboardButton(text="🗑️ Remove Landscape Poster", callback_data="posts:remove")],
        [InlineKeyboardButton(text="🔙 Back to Admin Panel", callback_data="posts:back")],
    ])

def confirm_remove_poster_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Remove", callback_data="posts:remove_confirm")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="posts:cancel")],
    ])

def force_sub_keyboard(channels: list, message: str = "") -> InlineKeyboardMarkup:
    rows=[]
    for c in channels:
        url=c["invite_url"] or ("https://t.me/"+c["username"].lstrip("@") if c["username"] else None)
        if url: rows.append([InlineKeyboardButton(text=c["join_label"],url=url)])
    rows.append([InlineKeyboardButton(text="✅ I HAVE JOINED — VERIFY",callback_data="verify_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
