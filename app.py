import functools
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta
import qrcode
import io
import base64
import mysql.connector
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename
import os


UPLOAD_FOLDER = "static/uploads/"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}


app = Flask(__name__)
app.secret_key = "123" 
app.permanent_session_lifetime = timedelta(hours=2)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Database Configuration
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "rental"
}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

# Middleware for authentication
def login_required(role="client"):
    def wrapper(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if 'user_id' not in session:
                flash("Please log in first.", "danger")
                return redirect(url_for('login'))
            if role == "admin" and session.get('user_role') != "admin":
                flash("Access denied.", "danger")
                return redirect(url_for('home'))
            return fn(*args, **kwargs)
        return decorated_view
    return wrapper

# ---------- Home Page ----------
@app.route('/')
def home():
    return render_template("home.html")

@app.route('/index')
def book_page():
    return render_template("index.html")

# ---------- User Authentication ----------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        contact_number = request.form.get('contact_number')
        address = request.form.get('address')
        password = request.form.get('password')

        # ✅ Validate that all fields are filled
        if not all([name, email, contact_number, address, password]):
            flash("⚠️ All fields are required!", "danger")
            return redirect(url_for('register'))

        # ✅ Validate email format
        if "@" not in email or "." not in email:
            flash("⚠️ Invalid email format!", "danger")
            return redirect(url_for('register'))

        # ✅ Validate phone number format (Only digits, max 15 characters)
        if not contact_number.isdigit() or len(contact_number) > 15:
            flash("⚠️ Invalid phone number! Only numbers allowed, max 15 digits.", "danger")
            return redirect(url_for('register'))

        # ✅ Securely hash password
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO users (name, email, contact_number, address, password, role) 
                VALUES (%s, %s, %s, %s, %s, 'client')
            """, (name, email, contact_number, address, hashed_password))
            conn.commit()
            flash("✅ Account created successfully! Please log in.", "success")
            return redirect(url_for('login'))
        except mysql.connector.IntegrityError:
            flash("⚠️ Email already exists. Try a different one.", "danger")
        finally:
            cursor.close()
            conn.close()

    return render_template("register.html")



@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and check_password_hash(user['password'], password):
            session.permanent = True
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['user_role'] = user['role']

            return redirect(url_for('admin_dashboard' if user['role'] == 'admin' else 'client_dashboard'))
        else:
            flash("❌ Invalid email or password!", "danger")

    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# ---------- Car Rental Booking ----------
@app.route('/book_rental', methods=['POST'])
@login_required("client")
def book_rental():
    data = request.json
    car_id = data.get("car_id")
    pickup_date = data.get("pickup_date")
    return_date = data.get("return_date")
    user_id = session.get("user_id")

    if not car_id or not pickup_date or not return_date:
        return jsonify({"error": "Missing required fields"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM cars WHERE id = %s AND status = 'available'", (car_id,))
    car = cursor.fetchone()

    if not car:
        conn.close()
        return jsonify({"error": "Car not available"}), 400

    # ✅ Generate QR Code Data
    qr_code_data = f"Car-{car_id}-Pickup-{pickup_date}-User-{user_id}"
    
    # ✅ Convert to Base64
    qr = qrcode.make(qr_code_data)
    buffered = io.BytesIO()
    qr.save(buffered, format="PNG")
    qr_base64 = base64.b64encode(buffered.getvalue()).decode()

    # ✅ Store Base64 QR Code in Database
    cursor.execute(
        "INSERT INTO car_rentals (user_id, car_id, pickup_date, return_date, qr_code, status) VALUES (%s, %s, %s, %s, %s, 'pending')",
        (user_id, car_id, pickup_date, return_date, qr_base64)
    )
    cursor.execute("UPDATE cars SET status = 'rented' WHERE id = %s", (car_id,))
    conn.commit()
    conn.close()

    return jsonify({"message": "Rental booked!", "qr_code": qr_base64})


@app.route('/my_rentals')
@login_required("client")
def my_rentals():
    user_id = session.get('user_id')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM car_rentals WHERE user_id = %s ORDER BY pickup_date DESC", (user_id,))
    rentals = cursor.fetchall()

    conn.close()
    return render_template("my_rentals.html", rentals=rentals)


# ---------- Car Management ----------
@app.route('/cars')
def get_available_cars():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, model, year, seats, plate_number, status, image FROM cars WHERE status='available'")
    cars = cursor.fetchall()
    cursor.close()
    conn.close()

    for car in cars:
        if not car["image"]:  # If no image, use default
            car["image"] = "default_car.png"

    return jsonify(cars)

@app.route('/get_cars', methods=['GET'])
@login_required("admin")
def get_cars():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM cars")
    cars = cursor.fetchall()
    conn.close()
    return render_template("admin_cars.html", cars=cars)



@app.route('/add_car', methods=['POST'])
@login_required("admin")
def add_car():
    name = request.form.get("car_name")
    model = request.form.get("car_model")
    year = request.form.get("car_year")
    plate_number = request.form.get("plate_number")
    seats = request.form.get("seats")
    
    if not all([name, model, year, plate_number, seats]):
        return jsonify({"error": "All fields are required!"}), 400

    # ✅ Fix: Handle Image Upload
    image = request.files.get("car_image")
    filename = "default_car.jpg"  # Default image if none is uploaded

    if image and allowed_file(image.filename):
        filename = secure_filename(image.filename)
        image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(
            "INSERT INTO cars (name, model, year, plate_number, seats, image, status) VALUES (%s, %s, %s, %s, %s, %s, 'available')",
            (name, model, year, plate_number, seats, filename)
        )
        conn.commit()
        return jsonify({"message": "🚗 Car added successfully!"})
    except mysql.connector.IntegrityError:
        return jsonify({"error": "Car with this plate number already exists!"}), 400
    finally:
        cursor.close()
        conn.close()


@app.route('/delete_car/<int:id>', methods=['DELETE'])
@login_required("admin")
def delete_car(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM cars WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    
    return jsonify({"message": "Car deleted successfully!"})

@app.route('/delete_rental/<int:id>', methods=['DELETE'])
@login_required("admin")
def delete_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status FROM car_rentals WHERE id = %s", (id,))
    rental = cursor.fetchone()

    if rental and rental[0] in ['approved', 'picked_up']:
        conn.close()
        return jsonify({"error": "Cannot cancel an approved or picked-up rental"}), 403

    cursor.execute("DELETE FROM car_rentals WHERE id = %s", (id,))
    conn.commit()
    conn.close()

    return jsonify({"message": "Rental deleted successfully!"})



# Approve Rental Request
@app.route('/approve_rental/<int:id>', methods=['PUT'])
@login_required("admin")
def approve_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE car_rentals SET status = 'approved' WHERE id = %s", (id,))
        conn.commit()
        return jsonify({"message": "Rental Approved!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# Reject Rental Request
@app.route('/reject_rental/<int:id>', methods=['PUT'])
@login_required("admin")
def reject_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE car_rentals SET status = 'rejected' WHERE id = %s", (id,))
        conn.commit()
        return jsonify({"message": "Rental Rejected!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# Mark as Picked Up
@app.route('/pickup_rental/<int:id>', methods=['PUT'])
@login_required("admin")
def pickup_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Ensure it's only picked up if approved
        cursor.execute("SELECT status FROM car_rentals WHERE id = %s", (id,))
        rental = cursor.fetchone()
        
        if rental and rental[0] == 'approved':
            cursor.execute("UPDATE car_rentals SET status = 'picked_up' WHERE id = %s", (id,))
            conn.commit()
            return jsonify({"message": "Car Picked Up!"})
        else:
            return jsonify({"error": "Rental must be approved before pickup."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# Mark as Returned
@app.route('/return_rental/<int:id>', methods=['PUT'])
@login_required("admin")
def return_rental(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Ensure it's only returned if picked up
        cursor.execute("SELECT car_id FROM car_rentals WHERE id = %s AND status = 'picked_up'", (id,))
        rental = cursor.fetchone()

        if rental:
            cursor.execute("UPDATE car_rentals SET status = 'returned' WHERE id = %s", (id,))
            cursor.execute("UPDATE cars SET status = 'available' WHERE id = %s", (rental[0],))
            conn.commit()
            return jsonify({"message": "Car Returned Successfully!"})
        else:
            return jsonify({"error": "Car must be picked up before returning."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ---------- Admin Dashboard ----------
@app.route('/dashboard')
@login_required("admin")
def admin_dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) as total FROM car_rentals")
    total_rentals = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as pending FROM car_rentals WHERE status = 'pending'")
    pending_rentals = cursor.fetchone()["pending"]

    cursor.execute("SELECT COUNT(*) as completed FROM car_rentals WHERE status = 'returned'")
    completed_rentals = cursor.fetchone()["completed"]

    cursor.execute("""
        SELECT cr.id, u.name as user_name, c.name as car_name, cr.pickup_date, cr.return_date, cr.qr_code, cr.status
        FROM car_rentals cr
        JOIN users u ON cr.user_id = u.id
        JOIN cars c ON cr.car_id = c.id
        ORDER BY cr.pickup_date DESC
        LIMIT 10
    """)
    rentals = cursor.fetchall()
    
    conn.close()
    return render_template("admin_dashboard.html", total_rentals=total_rentals, pending_rentals=pending_rentals, completed_rentals=completed_rentals, rentals=rentals)

@app.route('/dashboard_stats')
@login_required("admin")
def dashboard_stats():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Count total rentals
    cursor.execute("SELECT COUNT(*) as total FROM car_rentals")
    total = cursor.fetchone()["total"]

    # Count pending pickups
    cursor.execute("SELECT COUNT(*) as pending FROM car_rentals WHERE status = 'pending'")
    pending = cursor.fetchone()["pending"]

    # Count completed rentals (returned cars)
    cursor.execute("SELECT COUNT(*) as completed FROM car_rentals WHERE status = 'returned'")
    completed = cursor.fetchone()["completed"]

    conn.close()
    
    return jsonify({"total": total, "pending": pending, "completed": completed})


@app.route('/update_rental/<int:id>', methods=['PUT'])
@login_required("admin")
def update_rental(id):
    data = request.json
    new_pickup_date = data.get("pickup_date")

    if not new_pickup_date:
        return jsonify({"error": "Missing pickup date"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE car_rentals SET pickup_date = %s WHERE id = %s", (new_pickup_date, id))
        conn.commit()
        return jsonify({"message": "Rental updated successfully!"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/return_car/<int:rental_id>', methods=['PUT'])
@login_required("admin")
def return_car(rental_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get car_id associated with rental
    cursor.execute("SELECT car_id FROM car_rentals WHERE id = %s", (rental_id,))
    car = cursor.fetchone()

    if not car:
        conn.close()
        return jsonify({"error": "Rental not found"}), 404

    # Mark the rental as returned
    cursor.execute("UPDATE car_rentals SET status = 'returned' WHERE id = %s", (rental_id,))
    # Mark the car as available
    cursor.execute("UPDATE cars SET status = 'available' WHERE id = %s", (car[0],))
    
    conn.commit()
    conn.close()
    
    return jsonify({"message": "Car returned successfully and now available for booking!"})


@app.route('/analytics')
@login_required("admin")
def analytics():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT DATE(pickup_date) AS date, COUNT(*) AS count 
        FROM car_rentals 
        WHERE status = 'picked_up'
        GROUP BY DATE(pickup_date) 
        ORDER BY DATE(pickup_date) DESC 
        LIMIT 7
    """)
    daily_checkins = cursor.fetchall()

    cursor.execute("""
        SELECT DATE_FORMAT(pickup_date, '%Y-%m') AS month, COUNT(*) AS count 
        FROM car_rentals 
        GROUP BY month 
        ORDER BY month DESC 
        LIMIT 6
    """)
    monthly_rentals = cursor.fetchall()

    conn.close()

    return render_template("analytics.html", daily_checkins=daily_checkins, monthly_rentals=monthly_rentals)


@app.route('/admin_rentals')
@login_required("admin")
def admin_rentals():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT cr.id, u.name as user_name, c.name as car_name, cr.pickup_date, cr.return_date, cr.qr_code, cr.status
        FROM car_rentals cr
        JOIN users u ON cr.user_id = u.id
        JOIN cars c ON cr.car_id = c.id
        ORDER BY cr.pickup_date DESC
    """)
    rentals = cursor.fetchall()
    
    conn.close()
    return render_template("admin_rentals.html", rentals=rentals)

@app.route('/get_users', methods=['GET'])
@login_required("admin")
def get_users():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name, email, contact_number FROM users WHERE role = 'client'")
    users = cursor.fetchall()
    conn.close()
    return jsonify(users)


@app.route('/user_rentals/<int:user_id>')
@login_required("admin")
def user_rentals(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch user details
    cursor.execute("SELECT id, name, email, contact_number, address FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()

    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    # Fetch user rentals
    cursor.execute("""
        SELECT cr.id, c.name as car_name, cr.pickup_date, cr.return_date, cr.qr_code, cr.status
        FROM car_rentals cr
        JOIN cars c ON cr.car_id = c.id
        WHERE cr.user_id = %s
        ORDER BY cr.pickup_date DESC
    """, (user_id,))
    rentals = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("user_rentals.html", user=user, rentals=rentals)



@app.route('/rental_details/<int:rental_id>')
@login_required("client")
def rental_details(rental_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch rental details
    cursor.execute("""
        SELECT cr.id, c.name as car_name, cr.pickup_date, cr.return_date, cr.qr_code, cr.status
        FROM car_rentals cr
        JOIN cars c ON cr.car_id = c.id
        WHERE cr.id = %s
    """, (rental_id,))
    rental = cursor.fetchone()

    cursor.close()
    conn.close()

    if not rental:
        flash("Rental not found.", "danger")
        return redirect(url_for("client_dashboard"))

    return render_template("rental_details.html", rental=rental)


@app.route('/admin/rental_details/<int:user_id>')
@login_required("admin")
def admin_rental_details(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch user details
    cursor.execute("SELECT id, name, email, contact_number, address FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()

    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))

    # Fetch user rental history
    cursor.execute("""
        SELECT cr.id, c.name as car_name, cr.pickup_date, cr.return_date, cr.qr_code, cr.status
        FROM car_rentals cr
        JOIN cars c ON cr.car_id = c.id
        WHERE cr.user_id = %s
    """, (user_id,))
    rentals = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("admin_rental_details.html", user=user, rentals=rentals)


# ---------- Client Dashboard ----------
@app.route('/client_dashboard')
@login_required("client")
def client_dashboard():
    user_id = session.get('user_id')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT COUNT(*) as total FROM car_rentals WHERE user_id = %s", (user_id,))
    total_rentals = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as pending FROM car_rentals WHERE user_id = %s AND status = 'pending'", (user_id,))
    pending_rentals = cursor.fetchone()["pending"]

    cursor.execute("SELECT COUNT(*) as completed FROM car_rentals WHERE user_id = %s AND status = 'returned'", (user_id,))
    completed_rentals = cursor.fetchone()["completed"]

    cursor.execute("SELECT * FROM car_rentals WHERE user_id = %s ORDER BY pickup_date DESC", (user_id,))
    rentals = cursor.fetchall()

    conn.close()
    return render_template("client_dashboard.html", total_rentals=total_rentals, pending_rentals=pending_rentals, completed_rentals=completed_rentals, rentals=rentals)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
