"""Categories and alternate names for catalog technologies.

The TechStack table only stores names, so categories live here. Names nobody has categorised yet
fall under "Other tools" rather than being guessed.
"""

from __future__ import annotations

import re

from app.services.project_analysis import EXT_TO_LANGUAGE, LANGUAGE_TECH

CATEGORIES: list[tuple[str, str]] = [
    ("languages", "Programming languages"),
    ("frontend", "Frontend"),
    ("backend", "Backend"),
    ("databases", "Databases"),
    ("cloud", "Cloud & DevOps"),
    ("mobile", "Mobile"),
    ("data", "Data & AI"),
    ("testing", "Testing & tooling"),
    ("other", "Other tools"),
]
CATEGORY_LABELS = dict(CATEGORIES)

_GROUPS: dict[str, list[str]] = {
    "languages": [
        "Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Scala", "Groovy", "C#", "Visual Basic .NET", "F#",
        "Go", "Rust", "Swift", "Objective-C", "PHP", "Ruby", "Dart", "C", "C++", "HTML", "CSS", "Sass", "Less",
        "SQL", "Shell", "PowerShell", "R", "Julia", "Lua", "Perl", "Elixir", "Erlang", "Haskell", "Clojure", "OCaml",
        "Elm", "Zig", "Nim", "Solidity", "Assembly", "Fortran", "MATLAB", "CoffeeScript", "Verilog",
        "SystemVerilog", "VHDL", "GDScript",
    ],
    "frontend": [
        "React", "Angular", "Vue.js", "Next.js", "Nuxt", "Svelte", "SvelteKit", "SolidJS", "Astro", "Gatsby", "Remix",
        "Vite", "Webpack", "Parcel", "Tailwind CSS", "Bootstrap", "styled-components", "Emotion", "Material UI",
        "Chakra UI", "Ant Design", "jQuery", "Framer Motion", "Three.js", "D3.js", "Chart.js", "Recharts", "Leaflet",
        "Redux", "Zustand", "MobX", "TanStack Query", "React Router", "Axios", "Apollo GraphQL", "Alpine.js",
        "htmx", "GSAP", "Font Awesome", "EJS", "Handlebars", "Pug", "Twig", "Electron", "Tauri", "Yew", "Leptos",
        "Vue", "Streamlit", "Gradio",
    ],
    "backend": [
        "Node.js", "Express.js", "Koa", "Fastify", "hapi", "NestJS", "Socket.IO", "GraphQL", "REST", "tRPC",
        "Django", "Django REST framework", "Flask", "FastAPI", "Celery", "Pydantic", "Spring Boot", "Hibernate",
        "Ktor", "ASP.NET", ".NET", "Entity Framework", "Laravel", "Symfony", "Slim", "CodeIgniter", "CakePHP",
        "Livewire", "WordPress", "Ruby on Rails", "Sinatra", "Sidekiq", "Devise", "Gin", "Echo", "Fiber",
        "Gorilla Mux", "Chi", "Actix Web", "Axum", "Rocket", "Tokio", "Serde", "Stripe", "Protocol Buffer",
        "Windows Forms", "WPF", "JavaFX", "Swing", "Qt", "Tkinter", "Kivy", "Pygame", "Unity", "Godot", "Bevy",
        "Arduino", "PlatformIO",
    ],
    "databases": [
        "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "Firebase", "Supabase", "Prisma", "Sequelize",
        "TypeORM", "Drizzle", "SQLAlchemy", "GORM", "Diesel", "SQLx", "Room", "Core Data", "SwiftData",
        "SQL Server", "MariaDB", "Oracle", "DynamoDB", "Cassandra", "Elasticsearch", "Neo4j",
    ],
    "cloud": [
        "Docker", "Kubernetes", "AWS", "Azure", "Google Cloud", "Terraform", "Vercel", "Netlify", "Heroku",
        "Serverless Framework", "CI/CD", "GitHub Actions", "GitLab CI", "Travis CI", "Jenkins", "Azure Pipelines",
        "Bitbucket Pipelines", "Cloudflare", "Nginx", "Linux",
    ],
    "mobile": [
        "Android", "Jetpack Compose", "Retrofit", "SwiftUI", "UIKit", "AppKit", "MapKit", "ARKit", "Core ML",
        "SpriteKit", "SceneKit", "RealityKit", "HealthKit", "Combine", "Alamofire", "React Native", "Expo", "Ionic",
        "Capacitor", "Flutter", "Provider", "Riverpod", "Bloc", "GetX", "Dio", ".NET MAUI", "Xamarin", "CocoaPods",
        "Swift Package Manager", "Xcode",
    ],
    "data": [
        "TensorFlow", "Keras", "PyTorch", "scikit-learn", "pandas", "NumPy", "SciPy", "Polars", "Matplotlib",
        "Seaborn", "Plotly", "OpenCV", "Hugging Face", "NLTK", "spaCy", "XGBoost", "LightGBM", "LangChain", "OpenAI",
        "Jupyter", "Beautiful Soup", "Scrapy", "Selenium",
    ],
    "testing": [
        "Jest", "Vitest", "Mocha", "Cypress", "Playwright", "Testing Library", "pytest", "JUnit", "xUnit", "NUnit",
        "RSpec", "PHPUnit", "Git", "Maven", "Gradle", "CMake", "TypeScript ESLint", "ESLint", "Prettier",
    ],
}

_BY_NAME: dict[str, str] = {name.lower(): key for key, names in _GROUPS.items() for name in names}
for _lang in set(EXT_TO_LANGUAGE.values()):
    _tech = LANGUAGE_TECH.get(_lang, _lang)
    if _tech and _tech.lower() not in _BY_NAME:
        _BY_NAME[_tech.lower()] = "languages"

KNOWN_NAMES: dict[str, str] = {name.lower(): name for names in _GROUPS.values() for name in names}


def category_of(name: str) -> str:
    return _BY_NAME.get(name.lower(), "other")


# Other ways job descriptions write a technology. Keys are lowercase phrases matched on word boundaries.
JD_ALIASES: dict[str, str] = {
    "js": "JavaScript", "ecmascript": "JavaScript", "es6": "JavaScript", "ts": "TypeScript",
    "reactjs": "React", "react.js": "React", "nodejs": "Node.js", "node": "Node.js", "node js": "Node.js",
    "expressjs": "Express.js", "express.js": "Express.js", "nextjs": "Next.js", "next.js": "Next.js",
    "vue": "Vue.js", "vuejs": "Vue.js", "nuxt.js": "Nuxt", "nuxtjs": "Nuxt", "angularjs": "Angular",
    "nestjs": "NestJS", "nest.js": "NestJS", "sveltekit": "SvelteKit", "tailwind": "Tailwind CSS",
    "tailwindcss": "Tailwind CSS", "scss": "Sass", "mui": "Material UI", "material-ui": "Material UI",
    "react query": "TanStack Query", "tanstack": "TanStack Query", "golang": "Go", "csharp": "C#", "c sharp": "C#",
    "dotnet": ".NET", ".net core": ".NET", "asp.net core": "ASP.NET", "aspnet": "ASP.NET", "cpp": "C++",
    "objective c": "Objective-C", "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "psql": "PostgreSQL",
    "mongo": "MongoDB", "mssql": "SQL Server", "ms sql": "SQL Server", "sql server": "SQL Server",
    "dynamo": "DynamoDB", "elastic search": "Elasticsearch", "k8s": "Kubernetes", "amazon web services": "AWS",
    "gcp": "Google Cloud", "google cloud platform": "Google Cloud", "microsoft azure": "Azure",
    "ci/cd": "CI/CD", "cicd": "CI/CD", "ci cd": "CI/CD", "continuous integration": "CI/CD",
    "github actions": "GitHub Actions", "gitlab ci/cd": "GitLab CI", "restful": "REST", "rest api": "REST",
    "rest apis": "REST", "graphql": "GraphQL", "spring boot": "Spring Boot", "springboot": "Spring Boot",
    "ruby on rails": "Ruby on Rails", "rails": "Ruby on Rails", "drf": "Django REST framework",
    "sklearn": "scikit-learn", "scikit learn": "scikit-learn", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
    "huggingface": "Hugging Face", "jetpack compose": "Jetpack Compose", "react-native": "React Native",
    "swift ui": "SwiftUI", "playwright": "Playwright", "testing library": "Testing Library",
    "react testing library": "Testing Library", "junit5": "JUnit", "x unit": "xUnit", "terraform": "Terraform",
}

# Names that are also everyday words. They are offered for review, never added on their own,
# and only when written with a capital letter (Go, Swift, Spring), never as lowercase prose.
AMBIGUOUS: frozenset[str] = frozenset({
    "Go", "Swift", "Rust", "Dart", "C", "R", "Spring", "Express", "REST", "Unity", "Combine", "Room", "Provider",
    "Swing", "Echo", "Fiber", "Rocket", "Chi", "Gin", "Expo", "Bloc", "Retrofit", "Celery", "Elm", "Julia", "Ionic",
    "Electron", "Remix", "Gatsby", "Parcel", "Emotion", "Slim", "Devise", "Sinatra", "Assembly", "Shell", "Linux",
    "Oracle", "Pug", "Twig", "Tokio", "Serde", "Axum", "Yew", "Qt", "Kivy", "Godot", "Leaflet", "Less", "Bootstrap",
    "Mocha", "Cypress", "Prettier", "Dio",
})
# Names that are also ordinary words but are clear when capitalised ("React" vs "react to feedback").
CASE_SENSITIVE: frozenset[str] = frozenset({
    "React", "Angular", "Flutter", "Jest", "Stripe", "Selenium", "Hibernate", "Svelte", "Astro", "Redux", "Keras",
    "Scrapy", "Docker", "Jenkins", "Maven", "Gradle", "Laravel", "Django", "Flask", "Playwright", "Plotly", "Tauri",
})
AMBIGUOUS_TARGET: dict[str, str] = {"Spring": "Spring Boot", "Express": "Express.js"}


def term_pattern(term: str, case_sensitive: bool = False) -> re.Pattern[str]:
    """A term as a whole word: not glued to letters, digits, '+', '#' or a following '.word'."""
    flags = 0 if case_sensitive else re.I
    return re.compile(rf"(?<![\w+#./-]){re.escape(term)}(?![\w+#]|\.\w|-\w)", flags)
