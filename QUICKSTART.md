# SAHAL System - Quick Start Guide

## 🚀 Getting Started with Development

### Prerequisites
- Python 3.7+ installed
- Windows PowerShell or Command Prompt

### Installation Steps

#### 1. **Navigate to Project Directory**
```powershell
cd d:\WORK\APPS\SAHAL\SAHAL
```

#### 2. **Create Virtual Environment** (if not already created)
```powershell
python -m venv venv
```

#### 3. **Activate Virtual Environment**
```powershell
.\venv\Scripts\Activate.ps1
```
You should see `(venv)` prefix in your terminal.

#### 4. **Install Dependencies**
```powershell
pip install -r requirements.txt
```

#### 5. **Setup Environment Variables**
The `.env` file has been created. Edit it to configure:
```powershell
notepad .env
```

Key variables to set:
- `FLASK_ENV=development`
- `SECRET_KEY=your-secret-key`
- Database URLs (when ready)

#### 6. **Run the Application**

**Option A: Using the run script (Recommended)**
```powershell
.\run.ps1
```

**Option B: Manual startup**
```powershell
python app.py
```

### Accessing the Application

Once running, the application will be available at:
- **Homepage**: http://localhost:5000/
- **Talent**: http://localhost:5000/talent
- **Products**: http://localhost:5000/catalog
- **Login**: http://localhost:5000/login
- **Sign Up**: http://localhost:5000/signup
- **Admin Dashboard**: http://localhost:5000/dashboard/admin
- **Client Dashboard**: http://localhost:5000/dashboard/client
- **Worker Dashboard**: http://localhost:5000/dashboard/worker
- **Messages**: http://localhost:5000/chat
- **Quotations**: http://localhost:5000/quotations

## 📁 Project Structure

```
SAHAL/
├── venv/                    # Virtual environment
├── templates/               # Jinja2 templates
│   ├── base.html
│   ├── components/
│   ├── public/
│   ├── auth/
│   ├── dashboard/
│   └── errors/
├── static/                  # CSS, JS, images
│   ├── css/main.css
│   └── js/utils.js
├── app.py                   # Main Flask application
├── config.py                # Configuration settings
├── requirements.txt         # Python dependencies
├── .env                     # Environment variables (created)
├── .env.example             # Environment template
├── .gitignore               # Git ignore rules
├── run.ps1                  # PowerShell run script
├── init_project.py          # Project initialization
└── README.md                # Template documentation
```

## 🔧 Development Commands

### Virtual Environment
```powershell
# Activate
.\venv\Scripts\Activate.ps1

# Deactivate
deactivate
```

### Dependencies
```powershell
# Install
pip install -r requirements.txt

# Add new package
pip install package-name
pip freeze > requirements.txt

# List installed
pip list
```

### Running Flask
```powershell
# Development mode (auto-reload)
python app.py

# Using Flask CLI
$env:FLASK_APP = "app.py"
$env:FLASK_ENV = "development"
flask run

# With debug toolbar
$env:FLASK_DEBUG = "1"
flask run
```

## 🌐 Available Routes

### Public Routes
| Route | Description |
|-------|-------------|
| `/` | Homepage |
| `/talent` | Talent agency |
| `/catalog` | Products catalog |
| `/login` | Login page |
| `/signup` | Signup page |
| `/logout` | Logout |

### Dashboard Routes (Require Login)
| Route | Role | Description |
|-------|------|-------------|
| `/dashboard` | Any | Main dashboard |
| `/dashboard/admin` | Admin | Admin dashboard |
| `/dashboard/client` | Client | Client dashboard |
| `/dashboard/worker` | Worker | Worker dashboard |
| `/chat` | Any | Messages |
| `/quotations` | Any | Quotations |

### API Routes (JSON)
| Route | Method | Description |
|-------|--------|-------------|
| `/api/talent` | GET | Get talent data |
| `/api/products` | GET | Get products data |
| `/api/quotation` | POST | Create quotation |
| `/health` | GET | Health check |

## 🔍 Testing the Application

### Test Homepage
```
http://localhost:5000/
```
Should show the company homepage with hero section, services, and contact form.

### Test Authentication
1. Click "Get Started" button
2. Fill in the signup form
3. Select role (Client or Worker)
4. Submit to create test account
5. Should redirect to appropriate dashboard

### Test Talent Page
```
http://localhost:5000/talent
```
- Filter by category
- Filter by experience
- Click "View Profile" to see modal

### Test Catalog
```
http://localhost:5000/catalog
```
- Use sidebar filters
- Search products
- Add to cart

## 🐛 Troubleshooting

### Virtual Environment Issues
```powershell
# Reset virtual environment
Remove-Item -Recurse -Force venv
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Port 5000 Already in Use
```powershell
# Change port in app.py:
# app.run(host='0.0.0.0', port=5001)  # Use 5001 instead

# Or find and kill process using port 5000:
netstat -ano | findstr :5000
taskkill /PID <PID> /F
```

### Module Import Errors
```powershell
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt

# Or clear pip cache
pip install --no-cache-dir -r requirements.txt
```

### Template Not Found
- Ensure `templates/` directory exists
- Check file paths in Flask routes
- Verify .env file exists

## 📚 Next Steps for Development

1. **Database Setup** - Replace mock data with MySQL
   - Install `flask-sqlalchemy`
   - Create models for users, talents, products, etc.
   - Setup migrations with `alembic`

2. **Authentication System** - Implement proper login
   - Install `flask-login`, `flask-bcrypt`
   - Create user model and authentication logic
   - Add password hashing

3. **Form Validation** - Add server-side validation
   - Install `flask-wtf`
   - Create forms for login, signup, quotations

4. **File Uploads** - Handle talent photos, documents
   - Setup upload directory
   - Add file validation
   - Integrate with cloud storage (AWS S3)

5. **Email Notifications** - Send emails for confirmations
   - Setup `flask-mail`
   - Create email templates
   - Add email job queue

6. **REST API** - Build API endpoints
   - Add more `/api/` routes
   - Implement pagination and filtering
   - Add API authentication (JWT tokens)

## 📖 Documentation Files

- `README.md` - Template architecture guide
- `QUICKSTART.md` - This file
- `templates/` directory has inline comments
- `static/js/utils.js` - Documented utility functions

## 🆘 Getting Help

1. Check Flask documentation: https://flask.palletsprojects.com/
2. Check Jinja2 documentation: https://jinja.palletsprojects.com/
3. Check Tailwind CSS: https://tailwindcss.com/
4. Check Lucide Icons: https://lucide.dev/

---

**Happy coding! 🎉**

Last Updated: August 2024
