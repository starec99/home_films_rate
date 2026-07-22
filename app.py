import os
from flask import (
    Flask, render_template_string, redirect,
    url_for, request, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func
from sqlalchemy.orm import joinedload

# ---------------------------
# Настройки приложения
# ---------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'movies.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


# ---------------------------
# Ассоциационная таблица для жанров
# ---------------------------

movie_genre = db.Table(
    'movie_genre',
    db.Column('movie_id', db.Integer, db.ForeignKey('movie.id'), primary_key=True),
    db.Column('genre_id', db.Integer, db.ForeignKey('genre.id'), primary_key=True),
)


# ---------------------------
# Модели
# ---------------------------

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)  # роль администратора

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Genre(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    director = db.Column(db.String(255))
    poster = db.Column(db.String(255))  # имя файла обложки

    suggested_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    suggested_by = db.relationship('User', backref='suggested_movies')

    genres = db.relationship(
        'Genre',
        secondary=movie_genre,
        backref='movies'
    )


class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    score = db.Column(db.Float, nullable=False)   # 1.0–10.0
    review = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    movie_id = db.Column(db.Integer, db.ForeignKey('movie.id'), nullable=False)

    user = db.relationship('User', backref='ratings')
    movie = db.relationship('Movie', backref='ratings')


class WishlistItem(db.Model):
    """Элемент списка «хочу посмотреть», не связанный с таблицей Movie."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    note = db.Column(db.Text)               # опционально: заметка
    year = db.Column(db.String(10))        # опционально: год
    director = db.Column(db.String(255))   # опционально: режиссёр

    user = db.relationship('User', backref='wishlist_items')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'title', name='uq_user_title_wishlist'),
    )


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ---------------------------
# Вспомогательные функции
# ---------------------------

def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_genre_names(raw: str):
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(',')]
    return [p for p in parts if p]


def get_or_create_genre(name: str):
    g = Genre.query.filter(Genre.name.ilike(name)).first()
    if g:
        return g
    g = Genre(name=name)
    db.session.add(g)
    return g


def can_manage_movie(movie: Movie, user: User) -> bool:
    """Проверка: может ли пользователь редактировать/удалять фильм."""
    if not user.is_authenticated:
        return False
    if user.is_admin:
        return True
    return movie.suggested_by_id == user.id


# ---------------------------
# Роуты: регистрация и логин
# ---------------------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    template = """
    {% extends "base.html" %}
    {% block title %}Регистрация{% endblock %}
    {% block content %}
    <div class="row justify-content-center">
      <div class="col-md-4">
        <h2 class="mb-3">Регистрация</h2>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Логин (латиница/цифры)</label>
            <input type="text" name="username" class="form-control" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Имя для отображения</label>
            <input type="text" name="display_name" class="form-control" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Пароль</label>
            <input type="password" name="password" class="form-control" required>
          </div>
          <button type="submit" class="btn btn-primary w-100">Создать аккаунт</button>
        </form>
      </div>
    </div>
    {% endblock %}
    """
    if request.method == 'POST':
        username = request.form['username'].strip()
        display_name = request.form['display_name'].strip()
        password = request.form['password']

        if not username or not display_name or not password:
            flash('Заполните все поля', 'danger')
            return render_template_string(template)

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash('Такой логин уже существует', 'danger')
            return render_template_string(template)

        user = User(username=username, display_name=display_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Пользователь создан, теперь войдите', 'success')
        return redirect(url_for('login'))

    return render_template_string(template)


@app.route('/login', methods=['GET', 'POST'])
def login():
    template = """
    {% extends "base.html" %}
    {% block title %}Вход{% endblock %}
    {% block content %}
    <div class="row justify-content-center">
      <div class="col-md-4">
        <h2 class="mb-3">Вход</h2>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Логин</label>
            <input type="text" name="username" class="form-control" required>
          </div>
          <div class="mb-3">
            <label class="form-label">Пароль</label>
            <input type="password" name="password" class="form-control" required>
          </div>
          <button type="submit" class="btn btn-primary w-100">Войти</button>
        </form>
        <p class="mt-3 text-center">
          <a href="{{ url_for('register') }}">Создать аккаунт</a>
        </p>
      </div>
    </div>
    {% endblock %}
    """
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index'))
        flash('Неверный логин или пароль', 'danger')
    return render_template_string(template)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ---------------------------
# Админ-панель пользователей
# ---------------------------

@app.route('/admin/users')
@login_required
def admin_users():
    if not current_user.is_admin:
        flash('Эта страница доступна только администраторам', 'danger')
        return redirect(url_for('index'))

    users = User.query.order_by(User.username).all()

    template = """
    {% extends "base.html" %}
    {% block title %}Админ: пользователи{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Админ-панель: пользователи</h2>
      <a href="{{ url_for('index') }}" class="btn btn-outline-light btn-sm">На главную</a>
    </div>

    <table class="table table-dark table-striped table-bordered align-middle">
      <thead>
        <tr>
          <th>ID</th>
          <th>Логин</th>
          <th>Имя</th>
          <th>Роль</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
      {% for u in users %}
        <tr>
          <td>{{ u.id }}</td>
          <td>{{ u.username }}</td>
          <td>{{ u.display_name }}</td>
          <td>
            {% if u.is_admin %}
              <span class="badge bg-success">Админ</span>
            {% else %}
              <span class="badge bg-secondary">Пользователь</span>
            {% endif %}
          </td>
          <td>
            {% if u.id != current_user.id %}
              <form method="post"
                    action="{{ url_for('toggle_admin', user_id=u.id) }}"
                    class="d-inline">
                {% if u.is_admin %}
                  <button class="btn btn-sm btn-outline-warning">
                    Убрать права админа
                  </button>
                {% else %}
                  <button class="btn btn-sm btn-outline-success">
                    Сделать админом
                  </button>
                {% endif %}
              </form>
            {% else %}
              <small class="text-muted">Это вы</small>
            {% endif %}
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endblock %}
    """

    return render_template_string(template, users=users)


@app.route('/admin/users/<int:user_id>/toggle-admin', methods=['POST'])
@login_required
def toggle_admin(user_id):
    if not current_user.is_admin:
        flash('Эта операция доступна только администраторам', 'danger')
        return redirect(url_for('index'))

    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash('Нельзя изменять свои собственные права через эту форму', 'info')
        return redirect(url_for('admin_users'))

    user.is_admin = not user.is_admin
    db.session.commit()

    if user.is_admin:
        flash(f'Пользователь {user.username} теперь администратор', 'success')
    else:
        flash(f'У пользователя {user.username} убраны права администратора', 'info')

    return redirect(url_for('admin_users'))


# ---------------------------
# Профиль пользователя + «хочу посмотреть»
# ---------------------------

@app.route('/user/<int:user_id>')
@login_required
def user_profile(user_id):
    user = User.query.get_or_404(user_id)
    ratings = (
        Rating.query
        .filter_by(user_id=user.id)
        .join(Movie)
        .order_by(Rating.id.desc())
        .limit(10)
        .all()
    )
    avg_score = (
        db.session.query(func.avg(Rating.score))
        .filter_by(user_id=user.id)
        .scalar()
    )
    ratings_count = Rating.query.filter_by(user_id=user.id).count()

    wishlist_items = (
        WishlistItem.query
        .filter_by(user_id=user.id)
        .order_by(WishlistItem.title.asc())
        .all()
    )

    template = """
    {% extends "base.html" %}
    {% block title %}Профиль {{ user.display_name }}{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Профиль: {{ user.display_name }}</h2>
      <a href="{{ url_for('index') }}" class="btn btn-outline-light btn-sm">На главную</a>
    </div>

    <p><strong>Логин:</strong> {{ user.username }}</p>
    <p><strong>Роль:</strong> {% if user.is_admin %}Администратор{% else %}Обычный пользователь{% endif %}</p>
    <p><strong>Фильмов оценено:</strong> {{ ratings_count }}</p>
    <p><strong>Средний балл:</strong> {% if avg_score %}⭐ {{ '%.1f' % avg_score }}{% else %}—{% endif %}</p>

    <hr>

    <h4>Хочу посмотреть</h4>
    {% if current_user.id == user.id %}
      <form method="post" action="{{ url_for('add_wishlist_item', user_id=user.id) }}" class="mb-3">
        <div class="row g-2">
          <div class="col-md-4">
            <input type="text" name="title" class="form-control" placeholder="Название фильма" required>
          </div>
          <div class="col-md-2">
            <input type="text" name="year" class="form-control" placeholder="Год">
          </div>
          <div class="col-md-3">
            <input type="text" name="director" class="form-control" placeholder="Режиссёр">
          </div>
          <div class="col-md-3">
            <input type="text" name="note" class="form-control" placeholder="Заметка">
          </div>
        </div>
        <button class="btn btn-outline-light btn-sm mt-2">Добавить</button>
      </form>
    {% endif %}

    <table class="table table-dark table-striped table-bordered align-middle">
      <thead>
        <tr>
          <th>Название</th>
          <th>Год</th>
          <th>Режиссёр</th>
          <th>Заметка</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
      {% for item in wishlist_items %}
        <tr>
          <td>{{ item.title }}</td>
          <td>{{ item.year or '—' }}</td>
          <td>{{ item.director or '—' }}</td>
          <td>
            {% if item.note %}
              <small class="text-muted">{{ item.note }}</small>
            {% else %}
              —
            {% endif %}
          </td>
          <td>
            {% if current_user.id == user.id %}
              <a href="{{ url_for('edit_wishlist_item', item_id=item.id) }}"
                 class="btn btn-sm btn-outline-light me-1">
                Редактировать
              </a>

              <form method="post"
                    action="{{ url_for('delete_wishlist_item', item_id=item.id) }}"
                    class="d-inline"
                    onsubmit="return confirm('Удалить эту запись из «хочу посмотреть»?');">
                <button class="btn btn-sm btn-outline-danger">Удалить</button>
              </form>

              <form method="post"
                    action="{{ url_for('wishlist_to_movie', item_id=item.id) }}"
                    class="d-inline">
                <button class="btn btn-sm btn-outline-success">
                  В общий список
                </button>
              </form>
            {% else %}
              <small class="text-muted">—</small>
            {% endif %}
          </td>
        </tr>
      {% endfor %}
      {% if not wishlist_items %}
        <tr>
          <td colspan="5">Пока нет фильмов в списке «хочу посмотреть»</td>
        </tr>
      {% endif %}
      </tbody>
    </table>

    {% endblock %}
    """

    return render_template_string(
        template,
        user=user,
        ratings=ratings,
        avg_score=avg_score,
        ratings_count=ratings_count,
        wishlist_items=wishlist_items
    )


@app.route('/user/<int:user_id>/wishlist/add', methods=['POST'])
@login_required
def add_wishlist_item(user_id):
    if user_id != current_user.id:
        flash('Нельзя редактировать чужой список «хочу посмотреть»', 'danger')
        return redirect(url_for('user_profile', user_id=user_id))

    title = request.form.get('title', '').strip()
    year = request.form.get('year', '').strip()
    director = request.form.get('director', '').strip()
    note = request.form.get('note', '').strip()

    if not title:
        flash('Название фильма обязательно', 'danger')
        return redirect(url_for('user_profile', user_id=user_id))

    existing = WishlistItem.query.filter_by(user_id=current_user.id, title=title).first()
    if existing:
        flash('Этот фильм уже есть в списке «хочу посмотреть»', 'info')
        return redirect(url_for('user_profile', user_id=user_id))

    item = WishlistItem(
        user_id=current_user.id,
        title=title,
        year=year or None,
        director=director or None,
        note=note or None
    )
    db.session.add(item)
    db.session.commit()
    flash('Фильм добавлен в список «хочу посмотреть»', 'success')
    return redirect(url_for('user_profile', user_id=user_id))


@app.route('/wishlist/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_wishlist_item(item_id):
    item = WishlistItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        flash('Нельзя удалять чужие записи', 'danger')
        return redirect(url_for('user_profile', user_id=item.user_id))

    db.session.delete(item)
    db.session.commit()
    flash('Фильм удалён из списка «хочу посмотреть»', 'info')
    return redirect(url_for('user_profile', user_id=item.user_id))


@app.route('/wishlist/<int:item_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_wishlist_item(item_id):
    item = WishlistItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        flash('Нельзя редактировать чужие записи', 'danger')
        return redirect(url_for('user_profile', user_id=item.user_id))

    template = """
    {% extends "base.html" %}
    {% block title %}Редактировать «хочу посмотреть»{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Редактировать запись «хочу посмотреть»</h2>
      <a href="{{ url_for('user_profile', user_id=item.user_id) }}" class="btn btn-outline-light btn-sm">
        Назад к профилю
      </a>
    </div>

    <form method="post">
      <div class="mb-3">
        <label class="form-label">Название фильма</label>
        <input type="text" name="title" class="form-control" value="{{ item.title }}" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Год (опционально)</label>
        <input type="text" name="year" class="form-control" value="{{ item.year or '' }}">
      </div>
      <div class="mb-3">
        <label class="form-label">Режиссёр (опционально)</label>
        <input type="text" name="director" class="form-control" value="{{ item.director or '' }}">
      </div>
      <div class="mb-3">
        <label class="form-label">Заметка (опционально)</label>
        <textarea name="note" class="form-control" rows="3">{{ item.note or '' }}</textarea>
      </div>
      <button type="submit" class="btn btn-primary">Сохранить</button>
    </form>
    {% endblock %}
    """

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        year = request.form.get('year', '').strip()
        director = request.form.get('director', '').strip()
        note = request.form.get('note', '').strip()

        if not title:
            flash('Название фильма обязательно', 'danger')
            return render_template_string(template, item=item)

        existing = (
            WishlistItem.query
            .filter_by(user_id=current_user.id, title=title)
            .filter(WishlistItem.id != item.id)
            .first()
        )
        if existing:
            flash('У вас уже есть другая запись с таким названием', 'danger')
            return render_template_string(template, item=item)

        item.title = title
        item.year = year or None
        item.director = director or None
        item.note = note or None

        db.session.commit()
        flash('Запись «хочу посмотреть» обновлена', 'success')
        return redirect(url_for('user_profile', user_id=item.user_id))

    return render_template_string(template, item=item)


@app.route('/wishlist/<int:item_id>/to-movie', methods=['POST'])
@login_required
def wishlist_to_movie(item_id):
    item = WishlistItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        flash('Нельзя использовать чужую запись', 'danger')
        return redirect(url_for('user_profile', user_id=item.user_id))

    title = item.title.strip()
    director = (item.director or '').strip()

    if not title:
        flash('У записи нет названия, фильм не создан', 'danger')
        return redirect(url_for('user_profile', user_id=item.user_id))

    movie = Movie(
        title=title,
        director=director or None,
        poster=None,
        suggested_by=current_user
    )

    db.session.add(movie)
    db.session.delete(item)
    db.session.commit()

    flash('Фильм добавлен в общую таблицу и убран из «хочу посмотреть»', 'success')
    return redirect(url_for('index'))


# ---------------------------
# Таблица лидеров
# ---------------------------

@app.route('/leaders')
@login_required
def leaders():
    subq = (
        db.session.query(
            Movie.suggested_by_id.label('user_id'),
            func.avg(Rating.score).label('avg_score'),
            func.count(Movie.id).label('movie_count')
        )
        .join(Rating, Rating.movie_id == Movie.id)
        .filter(Movie.suggested_by_id.isnot(None))
        .group_by(Movie.suggested_by_id)
        .subquery()
    )

    rows = (
        db.session.query(
            User,
            subq.c.avg_score,
            subq.c.movie_count
        )
        .join(subq, User.id == subq.c.user_id)
        .order_by(subq.c.avg_score.desc())
        .all()
    )

    template = """
    {% extends "base.html" %}
    {% block title %}Таблица лидеров{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Таблица лидеров</h2>
      <a href="{{ url_for('index') }}" class="btn btn-outline-light btn-sm">На главную</a>
    </div>

    <p class="text-muted">
      Средний балл считается по оценкам всех пользователей для фильмов, которые предложил человек.
    </p>

    <table class="table table-dark table-striped table-bordered align-middle">
      <thead>
        <tr>
          <th>#</th>
          <th>Никнейм</th>
          <th>Фильмов предложено</th>
          <th>Средний рейтинг его фильмов</th>
        </tr>
      </thead>
      <tbody>
      {% for user, avg_score, movie_count in leaders %}
        <tr>
          <td>{{ loop.index }}</td>
          <td>
            <a class="link-light text-decoration-none"
               href="{{ url_for('user_profile', user_id=user.id) }}">
              {{ user.display_name }}
            </a>
          </td>
          <td>{{ movie_count }}</td>
          <td>⭐ {{ '%.1f' % avg_score }}</td>
        </tr>
      {% endfor %}
      {% if not leaders %}
        <tr><td colspan="4">Пока нет данных для таблицы лидеров</td></tr>
      {% endif %}
      </tbody>
    </table>
    {% endblock %}
    """

    return render_template_string(template, leaders=rows)


# ---------------------------
# Список фильмов + действия
# ---------------------------

@app.route('/', methods=['GET'])
@login_required
def index():
    template = """
    {% extends "base.html" %}
    {% block title %}Фильмы{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Рейтинг фильмов</h2>
      <div>
        <a href="{{ url_for('leaders') }}" class="btn btn-outline-light btn-sm me-2">Таблица лидеров</a>
        {% if current_user.is_admin %}
          <a href="{{ url_for('admin_users') }}" class="btn btn-outline-warning btn-sm">Админ: пользователи</a>
        {% endif %}
      </div>
    </div>

    <form class="row g-2 mb-4" method="get">
      <input type="hidden" name="sort" value="{{ sort }}">
      <input type="hidden" name="dir" value="{{ direction }}">
      <div class="col-md-4">
        <input type="text" name="title" class="form-control" placeholder="Название" value="{{ q_title }}">
      </div>
      <div class="col-md-3">
        <input type="text" name="genre" class="form-control" placeholder="Жанр (тег)" value="{{ q_genre }}">
      </div>
      <div class="col-md-3">
        <input type="text" name="director" class="form-control" placeholder="Режиссёр" value="{{ q_director }}">
      </div>
      <div class="col-md-2">
        <button class="btn btn-outline-light w-100">Поиск</button>
      </div>
    </form>

    <a href="{{ url_for('add_movie') }}" class="btn btn-outline-light mb-3">Добавить фильм</a>

    <table class="table table-dark table-striped table-bordered align-middle">
      <thead>
        <tr>
          <th>Обложка</th>

          <th>
            <a href="{{ url_for('index',
                                 title=q_title,
                                 genre=q_genre,
                                 director=q_director,
                                 sort='title',
                                 dir='desc' if sort == 'title' and direction == 'asc' else 'asc') }}"
               class="link-light text-decoration-none">
              Название
              {% if sort == 'title' %}
                {% if direction == 'asc' %}▲{% else %}▼{% endif %}
              {% endif %}
            </a>
          </th>

          <th>
            <a href="{{ url_for('index',
                                 title=q_title,
                                 genre=q_genre,
                                 director=q_director,
                                 sort='director',
                                 dir='desc' if sort == 'director' and direction == 'asc' else 'asc') }}"
               class="link-light text-decoration-none">
              Режиссёр
              {% if sort == 'director' %}
                {% if direction == 'asc' %}▲{% else %}▼{% endif %}
              {% endif %}
            </a>
          </th>

          <th>
            <a href="{{ url_for('index',
                                 title=q_title,
                                 genre=q_genre,
                                 director=q_director,
                                 sort='genre',
                                 dir='desc' if sort == 'genre' and direction == 'asc' else 'asc') }}"
               class="link-light text-decoration-none">
              Жанры
              {% if sort == 'genre' %}
                {% if direction == 'asc' %}▲{% else %}▼{% endif %}
              {% endif %}
            </a>
          </th>

          <th>
            <a href="{{ url_for('index',
                                 title=q_title,
                                 genre=q_genre,
                                 director=q_director,
                                 sort='rating',
                                 dir='desc' if sort == 'rating' and direction == 'asc' else 'asc') }}"
               class="link-light text-decoration-none">
              Наш средний
              {% if sort == 'rating' %}
                {% if direction == 'asc' %}▲{% else %}▼{% endif %}
              {% endif %}
            </a>
          </th>

          <th>Кто предложил</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
      {% for movie in movies %}
        <tr>
          <td style="width:80px">
            {% if movie.poster %}
              <img src="{{ url_for('static', filename='uploads/' ~ movie.poster) }}" class="img-fluid poster-thumb">
            {% endif %}
          </td>
          <td>
            <a class="link-light" href="{{ url_for('movie_detail', movie_id=movie.id) }}">
              {{ movie.title }}
            </a>
          </td>
          <td>{{ movie.director or '-' }}</td>
          <td>
            {% if movie.genres %}
              {% for g in movie.genres %}
                <span class="badge bg-warning text-dark me-1 mb-1">{{ g.name }}</span>
              {% endfor %}
            {% else %}
              -
            {% endif %}
          </td>
          <td>
            {% if avg_scores.get(movie.id) %}
              ⭐ {{ '%.1f' % avg_scores.get(movie.id) }}
            {% else %}
              -
            {% endif %}
          </td>
          <td>
            {% if movie.suggested_by %}
              <a class="link-light text-decoration-none"
                 href="{{ url_for('user_profile', user_id=movie.suggested_by.id) }}">
                {{ movie.suggested_by.display_name }}
              </a>
            {% else %}
              -
            {% endif %}
          </td>
          <td>
            {% if current_user.is_authenticated and (current_user.is_admin or movie.suggested_by_id == current_user.id) %}
              <a href="{{ url_for('edit_movie', movie_id=movie.id) }}"
                 class="btn btn-sm btn-outline-light me-1">
                Редактировать
              </a>
              <form method="post"
                    action="{{ url_for('delete_movie', movie_id=movie.id) }}"
                    class="d-inline"
                    onsubmit="return confirm('Точно удалить этот фильм и все его оценки?');">
                <button class="btn btn-sm btn-outline-danger">Удалить</button>
              </form>
            {% else %}
              <small class="text-muted">—</small>
            {% endif %}
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endblock %}
    """

    q_title = request.args.get('title', '').strip()
    q_genre = request.args.get('genre', '').strip()
    q_director = request.args.get('director', '').strip()
    sort = request.args.get('sort', 'title')
    direction = request.args.get('dir', 'asc')

    query = Movie.query.options(joinedload(Movie.genres), joinedload(Movie.suggested_by))

    if q_title:
        query = query.filter(Movie.title.ilike(f'%{q_title}%'))
    if q_director:
        query = query.filter(Movie.director.ilike(f'%{q_director}%'))
    if q_genre:
        query = query.join(Movie.genres).filter(Genre.name.ilike(f'%{q_genre}%'))

    if sort == 'title':
        order_col = Movie.title
    elif sort == 'director':
        order_col = Movie.director
    elif sort == 'genre':
        query = query.outerjoin(Movie.genres)
        order_col = Genre.name
    elif sort == 'rating':
        subq = (
            db.session.query(Rating.movie_id, func.avg(Rating.score).label('avg_score'))
            .group_by(Rating.movie_id)
            .subquery()
        )
        query = query.outerjoin(subq, Movie.id == subq.c.movie_id)
        order_col = subq.c.avg_score
    else:
        order_col = Movie.title

    if direction == 'desc':
        query = query.order_by(order_col.desc().nullslast())
    else:
        query = query.order_by(order_col.asc().nullslast())

    movies = query.all()
    avg_scores = dict(
        db.session.query(Rating.movie_id, func.avg(Rating.score))
        .group_by(Rating.movie_id)
        .all()
    )

    return render_template_string(
        template,
        movies=movies,
        avg_scores=avg_scores,
        q_title=q_title,
        q_genre=q_genre,
        q_director=q_director,
        sort=sort,
        direction=direction
    )


# ---------------------------
# Добавление/редактирование/удаление фильма
# ---------------------------


@app.route('/movies/add', methods=['GET', 'POST'])
@login_required
def add_movie():
    template = """
    {% extends "base.html" %}
    {% block title %}Добавить фильм{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Добавить фильм</h2>
      <a href="{{ url_for('index') }}" class="btn btn-outline-light btn-sm">Назад к списку</a>
    </div>

    <form method="post" enctype="multipart/form-data">
      <div class="mb-3">
        <label class="form-label">Название</label>
        <input type="text" name="title" class="form-control" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Режиссёр</label>
        <input type="text" name="director" class="form-control">
      </div>
      <div class="mb-3">
        <label class="form-label">Жанры (через запятую)</label>
        <input type="text" name="genres" class="form-control"
               placeholder="Например: боевик, научная фантастика, триллер">
        {% if all_genres %}
          <small class="text-muted d-block mt-2">
            Уже есть жанры:
            {% for g in all_genres %}
              <span class="badge bg-secondary me-1 genre-badge"
                    data-genre="{{ g.name }}">
                {{ g.name }}
              </span>
            {% endfor %}
          </small>
        {% endif %}
      </div>
      <div class="mb-3">
        <label class="form-label">Кто предложил</label>
        <select name="suggested_by_id" class="form-select">
          <option value="">— не выбрано —</option>
          {% for u in all_users %}
            <option value="{{ u.id }}"
                    {% if current_user.is_authenticated and u.id == current_user.id %}selected{% endif %}>
              {{ u.display_name }}
            </option>
          {% endfor %}
        </select>
      </div>
      <div class="mb-3">
        <label class="form-label">Обложка (jpg/png)</label>
        <input type="file" name="poster" class="form-control">
      </div>
      <button type="submit" class="btn btn-primary">Сохранить</button>
    </form>
    {% endblock %}

    {% block scripts %}
    {{ super() }}
    <script>
    document.addEventListener('DOMContentLoaded', function () {
      const genreInput = document.querySelector('input[name="genres"]');
      if (!genreInput) return;

      document.querySelectorAll('.genre-badge').forEach(function (badge) {
        badge.style.cursor = 'pointer';
        badge.addEventListener('click', function () {
          const genre = badge.dataset.genre;
          if (!genre) return;

          let current = genreInput.value || '';
          let parts = current.split(',')
            .map(function (p) { return p.trim(); })
            .filter(function (p) { return p.length > 0; });

          const lowered = parts.map(function (p) { return p.toLowerCase(); });
          if (!lowered.includes(genre.toLowerCase())) {
            parts.push(genre);
          }

          genreInput.value = parts.join(', ');
        });
      });
    });
    </script>
    {% endblock scripts %}
    """

    if request.method == 'POST':
        title = request.form['title'].strip()
        director = request.form.get('director', '').strip()
        genres_raw = request.form.get('genres', '')
        suggested_by_id = request.form.get('suggested_by_id') or None
        poster_file = request.files.get('poster')

        poster_filename = None
        if poster_file and poster_file.filename and allowed_file(poster_file.filename):
            filename = secure_filename(poster_file.filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            poster_file.save(save_path)
            poster_filename = filename

        if not title:
            flash('Название обязательно', 'danger')
        else:
            suggested_user = None
            if suggested_by_id:
                suggested_user = User.query.get(int(suggested_by_id))

            movie = Movie(
                title=title,
                director=director or None,
                poster=poster_filename,
                suggested_by=suggested_user
            )

            genre_names = parse_genre_names(genres_raw)
            for name in genre_names:
                g = get_or_create_genre(name)
                movie.genres.append(g)

            db.session.add(movie)
            db.session.commit()
            return redirect(url_for('index'))

    all_genres = Genre.query.order_by(Genre.name).all()
    all_users = User.query.order_by(User.display_name).all()
    return render_template_string(template, all_genres=all_genres, all_users=all_users)


@app.route('/movies/<int:movie_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_movie(movie_id):
    movie = Movie.query.options(joinedload(Movie.genres)).get_or_404(movie_id)

    if not can_manage_movie(movie, current_user):
        flash('У вас нет прав редактировать этот фильм', 'danger')
        return redirect(url_for('movie_detail', movie_id=movie.id))

    template = """
    {% extends "base.html" %}
    {% block title %}Редактировать фильм{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">Редактировать фильм</h2>
      <a href="{{ url_for('movie_detail', movie_id=movie.id) }}" class="btn btn-outline-light btn-sm">Назад к фильму</a>
    </div>

    <form method="post" enctype="multipart/form-data">
      <div class="mb-3">
        <label class="form-label">Название</label>
        <input type="text" name="title" class="form-control" value="{{ movie.title }}" required>
      </div>
      <div class="mb-3">
        <label class="form-label">Режиссёр</label>
        <input type="text" name="director" class="form-control" value="{{ movie.director or '' }}">
      </div>
      <div class="mb-3">
        <label class="form-label">Жанры (через запятую)</label>
        <input type="text" name="genres" class="form-control"
               value="{{ genres_text }}"
               placeholder="Например: боевик, научная фантастика, триллер">
      </div>
      <div class="mb-3">
        <label class="form-label">Обложка (jpg/png)</label>
        {% if movie.poster %}
          <p class="mb-1"><small class="text-muted">Текущая обложка: {{ movie.poster }}</small></p>
        {% endif %}
        <input type="file" name="poster" class="form-control">
        <small class="text-muted">Если не выбирать файл, останется старая обложка.</small>
      </div>
      <button type="submit" class="btn btn-primary">Сохранить</button>
    </form>
    {% endblock %}
    """

    if request.method == 'POST':
        title = request.form['title'].strip()
        director = request.form.get('director', '').strip()
        genres_raw = request.form.get('genres', '')
        poster_file = request.files.get('poster')

        if not title:
            flash('Название обязательно', 'danger')
            genres_text = genres_raw
            return render_template_string(template, movie=movie, genres_text=genres_text)

        movie.title = title
        movie.director = director or None

        movie.genres.clear()
        genre_names = parse_genre_names(genres_raw)
        for name in genre_names:
            g = get_or_create_genre(name)
            movie.genres.append(g)

        if poster_file and poster_file.filename and allowed_file(poster_file.filename):
            filename = secure_filename(poster_file.filename)
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            poster_file.save(save_path)
            movie.poster = filename

        db.session.commit()
        flash('Фильм обновлён', 'success')
        return redirect(url_for('movie_detail', movie_id=movie.id))

    genres_text = ', '.join(g.name for g in movie.genres)
    return render_template_string(template, movie=movie, genres_text=genres_text)


@app.route('/movies/<int:movie_id>/delete', methods=['POST'])
@login_required
def delete_movie(movie_id):
    movie = Movie.query.get_or_404(movie_id)

    if not can_manage_movie(movie, current_user):
        flash('У вас нет прав удалять этот фильм', 'danger')
        return redirect(url_for('movie_detail', movie_id=movie.id))

    Rating.query.filter_by(movie_id=movie.id).delete()
    db.session.delete(movie)
    db.session.commit()
    flash('Фильм удалён', 'info')
    return redirect(url_for('index'))


@app.route('/movies/<int:movie_id>', methods=['GET', 'POST'])
@login_required
def movie_detail(movie_id):
    template = """
    {% extends "base.html" %}
    {% block title %}{{ movie.title }}{% endblock %}
    {% block content %}
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h2 class="mb-0">{{ movie.title }}</h2>
      <a href="{{ url_for('index') }}" class="btn btn-outline-light btn-sm">Назад к списку</a>
    </div>

    <div class="row">
      <div class="col-md-3">
        {% if movie.poster %}
          <img src="{{ url_for('static', filename='uploads/' ~ movie.poster) }}" class="img-fluid mb-3">
        {% endif %}
      </div>
      <div class="col-md-9">
        <p><strong>Режиссёр:</strong> {{ movie.director or '—' }}</p>
        <p><strong>Жанры:</strong>
          {% if movie.genres %}
            {% for g in movie.genres %}
              <span class="badge bg-warning text-dark me-1 mb-1">{{ g.name }}</span>
            {% endfor %}
          {% else %}
            —
          {% endif %}
        </p>
        <p><strong>Кто предложил:</strong>
          {% if movie.suggested_by %}
            <a class="link-light text-decoration-none"
               href="{{ url_for('user_profile', user_id=movie.suggested_by.id) }}">
              {{ movie.suggested_by.display_name }}
            </a>
          {% else %}
            —
          {% endif %}
        </p>
        <p><strong>Средний балл:</strong> {% if avg_score %}⭐ {{ '%.1f' % avg_score }}{% else %}—{% endif %}</p>


        <hr>

        <h4>Моя оценка</h4>
        <form method="post">
          <div class="mb-3">
            <label class="form-label">Оценка</label>
            <div id="star-bar" class="d-flex align-items-center mb-2">
              {% for i in range(1, 11) %}
                <span class="star"
                      data-value="{{ i }}"
                      style="cursor:pointer;font-size:1.8rem;color:#ccc;margin-right:0.15rem;">
                  ★
                </span>
              {% endfor %}
              <span id="star-value" class="ms-2 text-muted">
                {{ user_rating.score if user_rating else '' }}
              </span>
            </div>

            <input type="hidden" name="score"
                   id="score-input"
                   value="{{ user_rating.score if user_rating else '' }}">
          </div>

          <div class="mb-3">
            <label class="form-label">Точная оценка (1–10, шаг 0.1)</label>
            <input type="number"
                   name="score_precise"
                   id="score-precise"
                   class="form-control"
                   min="1" max="10" step="0.1"
                   value="{{ user_rating.score if user_rating else '' }}">
          </div>

          <div class="mb-3">
            <label class="form-label">Отзыв</label>
            <textarea name="review" class="form-control" rows="3">{{ user_rating.review if user_rating else '' }}</textarea>
          </div>
          <button type="submit" class="btn btn-primary">Сохранить</button>
        </form>

        <hr>

        <h4>Оценки друзей</h4>
        <ul class="list-group">
          {% for r in all_ratings %}
            <li class="list-group-item">
              <strong>{{ r.user.display_name }}</strong> — {{ '%.1f' % r.score }}
              {% if r.review %}
                <br><small class="text-muted">{{ r.review }}</small>
              {% endif %}
            </li>
          {% endfor %}
          {% if not all_ratings %}
            <li class="list-group-item">Пока нет оценок</li>
          {% endif %}
        </ul>
      </div>
    </div>

    <script>
      document.addEventListener('DOMContentLoaded', function () {
        const stars = document.querySelectorAll('#star-bar .star');
        const scoreInput = document.getElementById('score-input');
        const scorePrecise = document.getElementById('score-precise');
        const starValue = document.getElementById('star-value');

        function highlightStars(value) {
          stars.forEach(star => {
            const v = parseInt(star.getAttribute('data-value'));
            star.style.color = (v <= value) ? '#ffc700' : '#ccc';
          });
        }

        let initial = parseFloat(scoreInput.value || scorePrecise.value || 0);
        if (!isNaN(initial) && initial > 0) {
          highlightStars(Math.round(initial));
        }

        stars.forEach(star => {
          star.addEventListener('click', function () {
            const value = parseInt(this.getAttribute('data-value'));
            scoreInput.value = value;
            scorePrecise.value = value.toFixed(1);
            starValue.textContent = value.toFixed(1);
            highlightStars(value);
          });
        });

        if (scorePrecise) {
          scorePrecise.addEventListener('input', function () {
            let v = parseFloat(this.value);
            if (isNaN(v)) {
              v = 0;
            }
            if (v < 1) v = 1;
            if (v > 10) v = 10;
            scoreInput.value = v.toFixed(1);
            starValue.textContent = v.toFixed(1);
            highlightStars(Math.round(v));
          });
        }
      });
    </script>

    {% endblock %}
    """

    movie = Movie.query.options(joinedload(Movie.genres), joinedload(Movie.suggested_by)).get_or_404(movie_id)
    user_rating = Rating.query.filter_by(movie_id=movie.id, user_id=current_user.id).first()

    if request.method == 'POST' and 'score_precise' in request.form:
        score_str = request.form.get('score_precise') or request.form.get('score')
        try:
            score = float(score_str)
        except (TypeError, ValueError):
            flash('Некорректная оценка', 'danger')
            return redirect(url_for('movie_detail', movie_id=movie.id))

        review = request.form.get('review', '').strip()
        if score < 1.0 or score > 10.0:
            flash('Оценка должна быть от 1 до 10', 'danger')
        else:
            if user_rating:
                user_rating.score = score
                user_rating.review = review
            else:
                user_rating = Rating(
                    score=score,
                    review=review,
                    user_id=current_user.id,
                    movie_id=movie.id
                )
                db.session.add(user_rating)
            db.session.commit()
            flash('Оценка сохранена', 'success')
            return redirect(url_for('index'))

    all_ratings = Rating.query.filter_by(movie_id=movie.id).join(User).all()
    avg_score = db.session.query(func.avg(Rating.score)).filter_by(movie_id=movie.id).scalar()

    return render_template_string(
        template,
        movie=movie,
        user_rating=user_rating,
        all_ratings=all_ratings,
        avg_score=avg_score
    )


# ---------------------------
# Инициализация БД и базовый админ
# ---------------------------

def init_db():
    """Создаёт таблицы, если их ещё нет, и базового администратора Starec."""
    db.create_all()

    admin_username = 'Starec'
    admin_display_name = 'Старец'
    admin_password = 'Starec130499'

    existing_admin = User.query.filter_by(username=admin_username).first()
    if not existing_admin:
        admin = User(
            username=admin_username,
            display_name=admin_display_name,
            is_admin=True
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()


with app.app_context():
    init_db()


# ---------------------------
# Точка входа
# ---------------------------

if __name__ == '__main__':
    os.makedirs(os.path.join(BASE_DIR, 'static', 'uploads'), exist_ok=True)
    app.run(debug=True)