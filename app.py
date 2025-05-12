from flask import Flask, render_template, request, redirect, session, url_for, flash
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
import os
from dotenv import load_dotenv
import requests
from bson.objectid import ObjectId
from collections import Counter
from datetime import datetime
from functools import wraps
from flask import abort
import openai

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

OMDB_API_KEY = os.getenv("OMDB_API_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")

client = MongoClient(os.getenv('MONGO_URI'))
db = client['reviews_app']
users = db['users']
reviews = db['reviews']
up_next = db['up_next']


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/users')
@admin_required
def admin_users():
    all_users = list(users.find({}))
    return render_template('admin_users.html', users=all_users)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@admin_required
def add_user():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        is_admin = 'is_admin' in request.form

        if users.find_one({'username': username}):
            flash('User already exists.')
            return redirect(url_for('admin_users'))

        users.insert_one({
            'username': username,
            'password': password,
            'is_admin': is_admin
        })
        flash('User added.')
        return redirect(url_for('admin_users'))

    return render_template('add_user.html')

@app.route('/admin/users/delete/<user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    users.delete_one({'_id': ObjectId(user_id)})
    flash('User deleted.')
    return redirect(url_for('admin_users'))


@app.route('/search')
def search():
    query = request.args.get('query', '').strip()
    search_type = request.args.get('type', 'reviews')

    if not query:
        flash("Enter a search term.")
        return redirect(url_for('dashboard'))

    # -- Book Search --
    if search_type == 'books':
        api_url = f"https://openlibrary.org/search.json?q={query}"
        try:
            response = requests.get(api_url)
            data = response.json()
            books = data.get('docs', [])[:10]
        except Exception as e:
            print("Open Library API error:", e)
            books = []
        return render_template('book_results.html', books=books, search_query=query)

    # -- Movie Search --
    if search_type == 'movies':
        api_url = f"http://www.omdbapi.com/?s={query}&apikey={OMDB_API_KEY}"
        try:
            response = requests.get(api_url)
            data = response.json()
            movies = data.get('Search', [])
        except Exception as e:
            print("OMDb API error:", e)
            movies = []
        return render_template('movie_results.html', movies=movies, search_query=query)

    # -- Default: search user reviews --
    if 'username' not in session:
        return redirect(url_for('login'))

    user_reviews = reviews.find({
        'username': session['username'],
        '$or': [
            {'title': {'$regex': query, '$options': 'i'}},
            {'content': {'$regex': query, '$options': 'i'}}
        ]
    })

    return render_template('dashboard.html', reviews=list(user_reviews), search_query=query)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])
        if users.find_one({'username': username}):
            flash("Username already exists")
            return redirect(url_for('register'))
        users.insert_one({'username': username, 'password': password})
        flash("Registration successful. Please log in.")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password'].strip()

        user = users.find_one({'username': username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            session['is_admin'] = user.get('is_admin', False)  # ✅ Store admin status in session
            flash("Login successful!")
            return redirect(url_for('dashboard'))

        flash("Invalid username or password.")
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    session.pop('is_admin', None)
    flash("Logged out")
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    recent_reviews = list(reviews.find({'username': session['username']}).sort('_id', -1).limit(5))
    up_next_books = list(up_next.find({'username': session['username'], 'type': 'Book'}).sort('_id', -1).limit(5))
    up_next_movies = list(up_next.find({'username': session['username'], 'type': 'Movie'}).sort('_id', -1).limit(5))

    return render_template('dashboard.html', reviews=recent_reviews, up_next_books=up_next_books, up_next_movies=up_next_movies)


@app.route('/create', methods=['GET', 'POST'])
def create_review():
    if request.method == 'POST':
        reviews.insert_one({
            'username': session['username'],
            'title': request.form['title'],
            'type': request.form['type'],
            'content': request.form['content'],
            'rating': float(request.form['rating'])
        })
        return redirect(url_for('dashboard'))
    return render_template('create_review.html')


@app.route('/edit/<review_id>', methods=['GET', 'POST'])
def edit_review(review_id):
    review = reviews.find_one({'_id': ObjectId(review_id)})
    if request.method == 'POST':
        reviews.update_one({'_id': ObjectId(review_id)}, {
            '$set': {
                'title': request.form['title'],
                'type': request.form['type'],
                'content': request.form['content'],
                'rating': float(request.form['rating'])
            }
        })
        return redirect(url_for('dashboard'))
    return render_template('edit_review.html', review=review)

@app.route('/delete/<review_id>')
def delete_review(review_id):
    reviews.delete_one({'_id': ObjectId(review_id)})
    return redirect(url_for('dashboard'))

from flask import request

@app.route('/save_review', methods=['POST'])
def save_review():
    if 'username' not in session:
        flash("You must be logged in to save a review.")
        return redirect(url_for('login'))

    title = request.form.get('title')
    review_type = request.form.get('type')
    content = request.form.get('content')

    reviews.insert_one({
        'username': session['username'],
        'title': title,
        'type': review_type,
        'content': content
    })

    flash("Saved to your reviews!")
    return redirect(url_for('dashboard'))

@app.route('/add_up_next', methods=['POST'])
def add_up_next():
    if 'username' not in session:
        flash("Log in to save to Up Next.")
        return redirect(url_for('login'))

    title = request.form['title']
    item_type = request.form['type']
    info = request.form.get('info', '')

    existing = up_next.find_one({
        'username': session['username'],
        'title': title,
        'type': item_type
    })

    if not existing:
        up_next.insert_one({
            'username': session['username'],
            'title': title,
            'type': item_type,
            'info': info
        })

    flash("Added to Up Next!")
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/up_next')
def view_up_next():
    if 'username' not in session:
        return redirect(url_for('login'))

    items = list(up_next.find({'username': session['username']}))
    return render_template('up_next.html', items=items)

@app.route('/remove_up_next', methods=['POST'])
def remove_up_next():
    if 'username' not in session:
        return redirect(url_for('login'))

    item_id = request.form['item_id']
    up_next.delete_one({'_id': ObjectId(item_id), 'username': session['username']})
    flash("Item removed from Up Next.")
    return redirect(url_for('view_up_next'))

@app.route('/convert_up_next', methods=['POST'])
def convert_up_next():
    if 'username' not in session:
        return redirect(url_for('login'))

    title = request.form['title']
    item_type = request.form['type']
    content = request.form['content']
    item_id = request.form['item_id']

    # Save to reviews
    reviews.insert_one({
        'username': session['username'],
        'title': title,
        'type': item_type,
        'content': content,
        'rating': None  # User can edit it later
    })

    # Remove from up_next
    up_next.delete_one({'_id': ObjectId(item_id), 'username': session['username']})

    flash("Item converted to a review.")
    return redirect(url_for('dashboard'))

@app.route('/reviews')
def reviews_page():
    if 'username' not in session:
        return redirect(url_for('login'))

    all_reviews = list(reviews.find({'username': session['username']}).sort('_id', -1))
    return render_template('reviews.html', reviews=all_reviews)

from collections import Counter

@app.route('/stats')
def stats():
    if 'username' not in session:
        return redirect(url_for('login'))

    user_reviews = list(reviews.find({'username': session['username']}))

    total_books = sum(1 for r in user_reviews if r['type'] == 'Book')
    total_movies = sum(1 for r in user_reviews if r['type'] == 'Movie')

    rated_reviews = [r for r in user_reviews if isinstance(r.get('rating'), (int, float))]
    average_rating = round(sum(r['rating'] for r in rated_reviews) / len(rated_reviews), 2) if rated_reviews else None

    top_reviews = sorted(rated_reviews, key=lambda x: x['rating'], reverse=True)[:3]
    lowest_review = min(rated_reviews, key=lambda x: x['rating']) if rated_reviews else None

    # Star rating distribution
    rating_counts = Counter([str(r['rating']) for r in rated_reviews])
    rating_labels = [str(round(i * 0.5, 1)) for i in range(2, 11)]  # 1.0 to 5.0
    rating_data = [rating_counts.get(label, 0) for label in rating_labels]

    # 📈 Reviews over time (by date)
    date_counts = Counter()
    for r in user_reviews:
        if r.get('_id'):
            created_date = r['_id'].generation_time.strftime('%Y-%m-%d')
            date_counts[created_date] += 1

    sorted_dates = sorted(date_counts)
    review_dates = sorted_dates
    review_counts = [date_counts[day] for day in sorted_dates]

    return render_template('stats.html',
                           total_books=total_books,
                           total_movies=total_movies,
                           average_rating=average_rating,
                           top_reviews=top_reviews,
                           lowest_review=lowest_review,
                           rating_labels=rating_labels,
                           rating_data=rating_data,
                           review_dates=review_dates,
                           review_counts=review_counts)

@app.route('/recommendations', methods=['GET', 'POST'])
def recommendations():
    recommendation = None
    if request.method == 'POST':
        prompt = request.form['prompt']

        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",  # or "gpt-4"
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that recommends books and movies."},
                    {"role": "user", "content": f"Recommend a book or movie based on this: {prompt}"}
                ],
                temperature=0.7,
                max_tokens=200
            )
            recommendation = response['choices'][0]['message']['content']
        except Exception as e:
            print("OpenAI API error:", e)
            recommendation = "Sorry, we couldn't fetch a recommendation right now."

    return render_template('recommendations.html', recommendation=recommendation)

if __name__ == '__main__':
    app.run(debug=True)
