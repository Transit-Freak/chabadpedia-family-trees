"""העלאת עץ לאתר. ברירת מחדל: הרצה יבשה. מבצע רק עם --yes וגם --i-have-community-approval.

עריכות אוטומטיות בחב"דפדיה דורשות תיאום עם הקהילה (אולם הדיונים / מדיניות הבוטים).
"""
from __future__ import annotations

import os


def upload_tree(client, cfg: dict, args) -> int:
    with open(args.file, encoding="utf-8") as fh:
        text = fh.read()
    print(f"יעד: {args.title}\nאורך: {len(text)} תווים\nתקציר: {args.summary}")
    if not args.yes:
        print("הרצה יבשה. להעלאה אמיתית: --yes --i-have-community-approval (אחרי דיון בקהילה).")
        return 0
    if not args.i_have_community_approval:
        print("לא מעלה: חסר הדגל --i-have-community-approval. העלאה המונית בלי תיאום תוחזר על ידי הקהילה.")
        return 3
    user = args.user or os.environ.get("CHABADPEDIA_USER")
    password = args.password or os.environ.get("CHABADPEDIA_PASSWORD")
    if not user or not password:
        print("חסרים פרטי התחברות (CHABADPEDIA_USER / CHABADPEDIA_PASSWORD).")
        return 3
    client.login(user, password)
    result = client.edit(args.title, text, args.summary, bot=True)
    print(result)
    return 0
