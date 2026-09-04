/**
 * SAHAL System - Utility JavaScript
 * Common functions and utilities for all pages
 */

// ===========================
// DOM Helper Functions
// ===========================

/**
 * Query DOM element
 */
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

/**
 * Show/Hide Elements
 */
function show(element) {
    if (typeof element === 'string') {
        element = $(element);
    }
    if (element) element.classList.remove('hidden');
}

function hide(element) {
    if (typeof element === 'string') {
        element = $(element);
    }
    if (element) element.classList.add('hidden');
}

function toggle(element) {
    if (typeof element === 'string') {
        element = $(element);
    }
    if (element) element.classList.toggle('hidden');
}

// ===========================
// Form Helper Functions
// ===========================

/**
 * Get form data as object
 */
function getFormData(formSelector) {
    const form = typeof formSelector === 'string' ? $(formSelector) : formSelector;
    const formData = new FormData(form);
    const data = {};
    formData.forEach((value, key) => {
        if (data[key]) {
            if (Array.isArray(data[key])) {
                data[key].push(value);
            } else {
                data[key] = [data[key], value];
            }
        } else {
            data[key] = value;
        }
    });
    return data;
}

/**
 * Validate email
 */
function isValidEmail(email) {
    const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return regex.test(email);
}

/**
 * Validate password strength
 */
function validatePassword(password) {
    const strongRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$/;
    const mediumRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d]{8,}$/;
    
    if (strongRegex.test(password)) return 'strong';
    if (mediumRegex.test(password)) return 'medium';
    return 'weak';
}

// ===========================
// API Helper Functions
// ===========================

/**
 * Fetch wrapper with error handling
 */
async function apiCall(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });

        if (!response.ok) {
            throw new Error(`API Error: ${response.status}`);
        }

        const data = await response.json();
        return { success: true, data };
    } catch (error) {
        console.error('API Error:', error);
        return { success: false, error: error.message };
    }
}

/**
 * Make GET request
 */
function apiGet(url) {
    return apiCall(url, { method: 'GET' });
}

/**
 * Make POST request
 */
function apiPost(url, body) {
    return apiCall(url, {
        method: 'POST',
        body: JSON.stringify(body)
    });
}

/**
 * Make PUT request
 */
function apiPut(url, body) {
    return apiCall(url, {
        method: 'PUT',
        body: JSON.stringify(body)
    });
}

/**
 * Make DELETE request
 */
function apiDelete(url) {
    return apiCall(url, { method: 'DELETE' });
}

// ===========================
// Notification Functions
// ===========================

/**
 * Show toast notification
 */
function showToast(message, type = 'info', duration = 3000) {
    const toast = document.createElement('div');
    toast.className = `alert alert-${type} fixed bottom-4 right-4 z-50 slide-in-left`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, duration);
}

/**
 * Show alert dialog
 */
function showAlert(message, type = 'info') {
    alert(message); // Could be enhanced with custom modal
}

/**
 * Show confirmation dialog
 */
function showConfirm(message, onConfirm, onCancel) {
    if (confirm(message)) {
        onConfirm();
    } else {
        onCancel && onCancel();
    }
}

// ===========================
// Date/Time Functions
// ===========================

/**
 * Format date to readable string
 */
function formatDate(date, format = 'MMM DD, YYYY') {
    if (typeof date === 'string') {
        date = new Date(date);
    }

    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    const day = String(date.getDate()).padStart(2, '0');
    const month = months[date.getMonth()];
    const year = date.getFullYear();
    const dayName = days[date.getDay()];

    return format
        .replace('DD', day)
        .replace('MMM', month)
        .replace('YYYY', year)
        .replace('ddd', dayName);
}

/**
 * Format time to readable string
 */
function formatTime(date) {
    if (typeof date === 'string') {
        date = new Date(date);
    }
    return date.toLocaleTimeString('en-US', { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
}

/**
 * Get relative time (e.g., "2 hours ago")
 */
function getRelativeTime(date) {
    if (typeof date === 'string') {
        date = new Date(date);
    }

    const now = new Date();
    const secondsPast = (now - date) / 1000;

    if (secondsPast < 60) return 'now';
    if (secondsPast < 3600) return Math.floor(secondsPast / 60) + ' minutes ago';
    if (secondsPast < 86400) return Math.floor(secondsPast / 3600) + ' hours ago';
    if (secondsPast < 604800) return Math.floor(secondsPast / 86400) + ' days ago';
    
    return formatDate(date);
}

// ===========================
// Number/Currency Functions
// ===========================

/**
 * Format number as currency
 */
function formatCurrency(amount, currency = 'USD') {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency
    }).format(amount);
}

/**
 * Format number with thousands separator
 */
function formatNumber(number, decimals = 0) {
    return Number(number).toLocaleString('en-US', {
        maximumFractionDigits: decimals,
        minimumFractionDigits: decimals
    });
}

// ===========================
// Storage Functions
// ===========================

/**
 * Local storage wrapper
 */
const storage = {
    set: (key, value) => localStorage.setItem(key, JSON.stringify(value)),
    get: (key) => {
        const item = localStorage.getItem(key);
        return item ? JSON.parse(item) : null;
    },
    remove: (key) => localStorage.removeItem(key),
    clear: () => localStorage.clear()
};

// ===========================
// Utility Functions
// ===========================

/**
 * Deep clone object
 */
function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
}

/**
 * Debounce function
 */
function debounce(func, delay) {
    let timeoutId;
    return function (...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => func(...args), delay);
    };
}

/**
 * Throttle function
 */
function throttle(func, limit) {
    let inThrottle;
    return function (...args) {
        if (!inThrottle) {
            func(...args);
            inThrottle = true;
            setTimeout(() => inThrottle = false, limit);
        }
    };
}

/**
 * Generate random ID
 */
function generateId() {
    return Math.random().toString(36).substr(2, 9);
}

/**
 * Check if element is in viewport
 */
function isInViewport(element) {
    const rect = element.getBoundingClientRect();
    return (
        rect.top >= 0 &&
        rect.left >= 0 &&
        rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
        rect.right <= (window.innerWidth || document.documentElement.clientWidth)
    );
}

// ===========================
// Event Listeners
// ===========================

/**
 * Initialize when DOM is ready
 */
document.addEventListener('DOMContentLoaded', function() {
    // Auto-reinitialize Lucide icons if needed
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }

    // Setup common event handlers
    setupFormValidation();
    setupMobileMenu();
});

/**
 * Setup form validation
 */
function setupFormValidation() {
    document.querySelectorAll('form').forEach(form => {
        form.addEventListener('submit', function(e) {
            // Add custom validation here if needed
        });
    });
}

/**
 * Setup mobile menu toggle
 */
function setupMobileMenu() {
    const menuBtn = document.getElementById('mobile-menu-btn');
    const menu = document.getElementById('mobile-menu');

    if (menuBtn && menu) {
        menuBtn.addEventListener('click', () => {
            menu.classList.toggle('hidden');
        });

        // Close menu when clicking on a link
        menu.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                menu.classList.add('hidden');
            });
        });
    }
}

// ===========================
// Export for module usage
// ===========================
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        $, $$, show, hide, toggle,
        getFormData, isValidEmail, validatePassword,
        apiCall, apiGet, apiPost, apiPut, apiDelete,
        showToast, showAlert, showConfirm,
        formatDate, formatTime, getRelativeTime,
        formatCurrency, formatNumber,
        storage, deepClone, debounce, throttle, generateId,
        isInViewport
    };
}
