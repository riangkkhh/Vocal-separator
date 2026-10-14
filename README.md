# הפרדת קול ופלייבק

אתר שמקבל שיר ומחזיר שני קבצים: ווקאל בלבד ופלייבק בלבד.

- **האתר (דף ההעלאה):** `docs/index.html`, מתארח בחינם ב-GitHub Pages.
- **השרת והעיבוד:** `backend/app.py`, רץ על Modal (GPU לפי שימוש), ונפרס אוטומטית מ-GitHub Actions.
- **הכול נעשה דרך הדפדפן, בלי להתקין כלום על המחשב.**

## התקנה (פעם אחת)

### 1. חשבון Modal
1. נרשמים ב-https://modal.com (תוכנית Starter, כוללת קרדיט חודשי חינמי).
2. **לא מחברים כרטיס אשראי.**
3. נכנסים ל-Settings, ואז API Tokens, ויוצרים טוקן חדש. נשמרים שני ערכים: Token ID ו-Token Secret.
4. ב-Settings של ה-Workspace מגדירים Budget ו-Spend limit נמוכים (למשל 0 או 1 דולר) כשכבת הגנה נוספת.

### 2. ריפו ב-GitHub
1. יוצרים ריפו **ציבורי** חדש (נדרש כדי ש-GitHub Pages יהיה חינמי).
2. מעלים אליו את כל הקבצים (Add file, ואז Upload files). התיקייה `.github/workflows/deploy-backend.yml` חייבת להיות במקומה. אם תיקיות שמתחילות בנקודה לא עולות בגרירה, יוצרים אותה ידנית: Add file, Create new file, ובשם הקובץ כותבים `.github/workflows/deploy-backend.yml` ומדביקים את התוכן.
3. ב-Settings, ואז Secrets and variables, ואז Actions, מוסיפים שני Secrets:
   - `MODAL_TOKEN_ID`
   - `MODAL_TOKEN_SECRET`

### 3. פריסת השרת
1. נכנסים ללשונית **Actions**, בוחרים "Deploy backend to Modal", ולוחצים **Run workflow**.
2. ההרצה הראשונה לוקחת כמה דקות (מורידה את המודלים לתוך התמונה).
3. בסוף הלוג של הצעד Deploy מופיעה כתובת שנראית כך:
   `https://<workspace>--vocal-separator-web.modal.run`
   מעתיקים אותה.

### 4. חיבור האתר לשרת
1. פותחים את `docs/index.html` בגיטהאב (סמל העיפרון).
2. מחליפים את השורה `const API = "..."` בכתובת שהעתקתם (בלי `/` בסוף), ושומרים (Commit).
3. ב-Settings, ואז Pages, בוחרים: Source = Deploy from a branch, Branch = `main`, תיקייה `/docs`.
4. אחרי דקה-שתיים האתר זמין בכתובת ש-GitHub מציג שם.

## הגדרות (בראש `backend/app.py`)
| הגדרה | ברירת מחדל | משמעות |
|---|---|---|
| `MONTHLY_LIMIT` | 300 | תקרה גלובלית של שירים בחודש |
| `COOLDOWN_DAYS` | 7 | שיר אחד למשתמש בשבוע |
| `MAX_DURATION_SEC` | 480 | אורך שיר מקסימלי |
| `RETENTION_SEC` | 7200 | מחיקת קבצים אחרי שעתיים |
| `IP_SALT` | | להחליף בטקסט אקראי כלשהו |

אחרי כל שינוי בתיקיית `backend`, הפריסה רצה מחדש אוטומטית.

## אם משהו נכשל
נכנסים ל-Actions, פותחים את ההרצה שנכשלה, ומעתיקים את שורת השגיאה. לוגים של העיבוד עצמו נמצאים בדשבורד של Modal תחת האפליקציה `vocal-separator`.
