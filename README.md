# SAHAL System - Frontend Template Architecture

Complete Jinja2 template layer for the SAHAL enterprise web application.

## 📁 Directory Structure

```
SAHAL/
├── templates/
│   ├── base.html                    # Main template shell with dynamic layout switching
│   ├── components/
│   │   ├── navbar.html              # Public & dashboard navigation bars
│   │   ├── sidebar.html             # Dashboard sidebar with navigation menu
│   │   └── footer.html              # Public pages footer
│   ├── public/
│   │   ├── index.html               # Company homepage with hero, services, portfolio, testimonials
│   │   ├── talent.html              # Talent agency with filterable talent grid & modal previews
│   │   └── catalog.html             # Products catalog with search, filtering, and e-commerce grid
│   ├── auth/
│   │   ├── login.html               # Centered login form with role selector
│   │   └── signup.html              # Role-based signup (Client vs Worker/Talent)
│   └── dashboard/
│       ├── admin.html               # Admin dashboard with stats, activity feed, quotation table
│       ├── client.html              # Client portal with bookings, quotes, messaging
│       ├── worker.html              # Worker/talent dashboard with job grid, calendar, earnings
│       ├── chat.html                # Two-pane messaging interface
│       └── quotations.html          # Quotation & invoice management
├── static/
│   ├── css/
│   │   └── main.css                 # Global styles, utilities, animations
│   └── js/
│       └── utils.js                 # Helper functions and utilities
└── README.md                        # This file
```

## 🎨 Design System

### Color Palette
- **Primary**: Blue (#2563eb) - Main actions, links
- **Secondary**: Slate (#1e293b) - Dark backgrounds
- **Success**: Green (#16a34a) - Confirmations, positive states
- **Warning**: Orange (#ea580c) - Alerts, pending states
- **Danger**: Red (#dc2626) - Errors, destructive actions

### Typography
- **Font Family**: System fonts (SF Pro, Segoe UI, Ubuntu)
- **Headings**: Bold (700), ranging from 1.125rem to 1.875rem
- **Body**: Regular (400), 1rem base size
- **Small Text**: 0.875rem for secondary information

### Components
- **Cards**: White background, subtle shadows, hover effects
- **Buttons**: Primary, outline, and secondary variants
- **Forms**: Full-width inputs with focus ring states
- **Badges**: Colored backgrounds for status indicators
- **Alerts**: Themed for info, success, warning, danger

## 📄 Template Overview

### Base Template (`base.html`)
- Dynamic layout switching between public and dashboard modes
- Tailwind CSS integration via CDN
- Lucide Icons for consistent iconography
- Alpine.js for lightweight interactivity
- Responsive mobile handling
- Standard Jinja2 blocks: `title`, `content`, `extra_css`, `extra_js`

**Key Variables:**
- `is_dashboard` - Boolean to toggle between public/dashboard layout
- `user` - User object with name, initials, role

### Public Pages

#### Homepage (`public/index.html`)
- Hero section with CTA buttons
- Services breakdown (Talent, Products, Solutions)
- Interactive portfolio gallery grid
- Client testimonials with ratings
- Contact form section

#### Talent Agency (`public/talent.html`)
- Filterable talent grid (4 columns)
- Search + category/experience filters
- Tag-based filtering (Featured, Available, etc.)
- Talent cards with ratings and details
- Modal for detailed talent profile preview
- Pagination controls

#### Products Catalog (`public/catalog.html`)
- Sidebar with search and multiple filters
- Products grid with stock badges
- Wishlist toggle on each product
- Price display with discounts
- Category and availability filters
- Sort options
- Responsive mobile-friendly layout

### Authentication Pages

#### Login (`auth/login.html`)
- Centered card layout
- Email + password fields
- Remember me checkbox
- Forgot password link
- Role selector (Client/Worker)
- Social login options (Google, Facebook)
- Sign-up redirect link

#### Signup (`auth/signup.html`)
- Role-based form switching (Client vs Worker)
- Client form: Name, company, email, phone, password
- Worker form: Name, profession, bio, email, phone, password
- Tab-based UI for role selection
- Terms & conditions checkbox
- Password strength indicator

### Dashboard Pages

#### Admin Dashboard (`dashboard/admin.html`)
- **Stats Cards** (4 columns):
  - Total Revenue with trend
  - Active Bookings count
  - Talent Roster size
  - Pending Quotations
- **System Activity Feed**: Real-time activity log
- **Top Performers**: Ranked list with percentages
- **Quick Actions**: Common admin tasks
- **Pending Quotations Table**: Line-item quotations with actions

#### Client Dashboard (`dashboard/client.html`)
- **Quick Stats** (3 columns):
  - Active Projects
  - Pending Quotes
  - Unread Messages
- **Active Bookings Cards**: Project details with status
- **Quote Requests**: List of pending quotes
- **Messages Sidebar**: Recent conversations
- **Upcoming Events**: Calendar preview
- **Quick Action Links**: Common client tasks

#### Worker Dashboard (`dashboard/worker.html`)
- **Performance Stats** (4 columns):
  - Completed Jobs
  - Upcoming Jobs
  - Total Earnings
  - Rating (with stars)
- **Assigned Jobs Grid**: Job cards with progress bars
- **Weekly Schedule**: Upcoming assignments
- **Performance Metrics**: Completion rate, satisfaction
- **Earnings Summary**: Monthly earnings and withdrawal

#### Chat (`dashboard/chat.html`)
- **Two-pane Layout**:
  - Left: Conversation list with search
  - Right: Active message thread
- **Features**:
  - User avatars with activity status
  - Message timestamps
  - Unread indicators
  - Message input with attachments
  - Action buttons (call, video)

#### Quotations (`dashboard/quotations.html`)
- **Quotation List**: Filterable by status, date
- **Line-item Quotations**: Detailed views with amounts
- **Status Badges**: Draft, Sent, Accepted, Rejected
- **Quick Create Form**: Sidebar form for new quotations
- **PDF Download**: Export quotations and invoices
- **Templates**: Reusable quotation templates

### Components

#### Navbar (`components/navbar.html`)
**Dashboard Version:**
- Logo and menu toggle
- Notifications dropdown
- User profile menu
- Logout option

**Public Version:**
- Logo/branding
- Navigation links (Home, Talent, Products, Contact)
- Sign In / Sign Up buttons
- Mobile hamburger menu

#### Sidebar (`components/sidebar.html`)
- User profile section with role indicator
- Hierarchical navigation menu:
  - Dashboard
  - Operations (Talent, Products, Bookings)
  - Transactions (Quotations, Invoices)
  - Communication (Messages)
  - Administration (Admin only)
- Settings link at bottom
- Admin-only features with conditional rendering

#### Footer (`components/footer.html`)
- Company info section
- Quick links
- Support section
- Legal links
- Social media icons
- Copyright notice

## 🎯 Key Features

### Responsive Design
- Mobile-first approach
- Breakpoints: 768px (md), 1024px (lg)
- Hamburger menu on mobile
- Sidebar collapses on smaller screens
- Touch-friendly button sizes

### Interactive Elements
- Alpine.js for DOM manipulation
- Form validation with error states
- Modal dialogs for previews
- Dropdown menus with keyboard support
- Smooth animations and transitions

### Accessibility
- Semantic HTML structure
- ARIA labels for icons
- Keyboard navigation support
- Color contrast compliance
- Focus states on interactive elements

### Performance Optimized
- CSS framework via CDN
- Minimal inline JavaScript
- Icon library deferred loading
- Responsive image placeholders
- CSS animations instead of JavaScript

## 🔧 Flask Integration

### Passing Data to Templates

```python
# Example Flask route
@app.route('/dashboard')
def dashboard():
    return render_template('dashboard/admin.html', 
        user={
            'name': 'John Doe',
            'initials': 'JD',
            'role': 'admin'
        },
        is_dashboard=True
    )
```

### Template Variables Reference

| Variable | Type | Used In | Purpose |
|----------|------|---------|---------|
| `user` | dict | All dashboard pages | Current user info |
| `is_dashboard` | bool | base.html | Layout mode |
| `talents` | list | talent.html | Talent gallery data |
| `products` | list | catalog.html | Product grid data |
| `bookings` | list | client.html | Client bookings |
| `quotations` | list | quotations.html | Quote list |

## 📝 Customization Guide

### Changing Colors
Edit `:root` variables in `static/css/main.css`:
```css
:root {
    --primary-color: #2563eb;
    --secondary-color: #1e293b;
    /* ... */
}
```

### Adding New Pages
1. Create HTML file extending `base.html`
2. Set `is_dashboard` variable if using dashboard layout
3. Include components as needed
4. Define content in `{% block content %}`

### Modifying Components
Edit component files in `templates/components/`:
- Changes apply globally to all using pages
- Update Jinja2 variables as needed
- Test across different screen sizes

## 🚀 Getting Started

### Setup
1. Install Flask and Jinja2
2. Copy template files to Flask `templates/` directory
3. Copy static files to Flask `static/` directory
4. Ensure Tailwind CDN and Lucide icons are accessible

### Running Locally
```bash
# Flask development server
python app.py

# Access templates
# Public: http://localhost:5000/
# Login: http://localhost:5000/login
# Dashboard: http://localhost:5000/dashboard
```

### Building Backend Routes
Connect templates to Flask routes in your app:
```python
# Public routes
@app.route('/')
@app.route('/talent')
@app.route('/catalog')

# Auth routes
@app.route('/login', methods=['GET', 'POST'])
@app.route('/signup', methods=['GET', 'POST'])

# Dashboard routes
@app.route('/dashboard')
@app.route('/chat')
@app.route('/quotations')
```

## 📚 Dependencies

### Frontend Libraries
- **Tailwind CSS** - Utility-first CSS framework
- **Lucide Icons** - Beautiful, consistent icon set
- **Alpine.js** - Lightweight JavaScript framework
- **Jinja2** - Template engine (Flask built-in)

### No build step required! Everything runs in the browser via CDN.

## 🎓 Best Practices

1. **Keep templates modular** - Use components for reusable sections
2. **Use Jinja2 loops** - For dynamic content rendering
3. **Conditional rendering** - Use `{% if %}` for role-based UI
4. **Consistent naming** - Follow template naming conventions
5. **Responsive first** - Test on mobile devices
6. **Accessibility** - Include proper ARIA labels
7. **Performance** - Minimize inline scripts, use utilities

## 📖 Template Syntax Examples

### Loops
```html
{% for item in items %}
    <div>{{ item.name }}</div>
{% endfor %}
```

### Conditionals
```html
{% if user.role == 'admin' %}
    <button>Admin Action</button>
{% endif %}
```

### Variables
```html
<p>{{ user.name }}</p>
<p>{{ user.email or 'No email' }}</p>
```

### Includes
```html
{% include 'components/navbar.html' %}
```

### Extends
```html
{% extends 'base.html' %}
```

---

**Version**: 1.0  
**Last Updated**: August 2024  
**Maintainer**: SAHAL Development Team
