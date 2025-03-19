from werkzeug.security import generate_password_hash

# Generate a hashed password using the default method (pbkdf2:sha256)
hashed_password = generate_password_hash('admin111')
print(hashed_password)