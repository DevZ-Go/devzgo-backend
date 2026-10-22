"""Project analysis: languages + frameworks from workspace files.

Deterministic only (no LLM). Called after ZIP extract. Tech comes from file extensions, dependency
manifests, config files and source imports; names the catalog does not have yet are added to it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.workspace_extract import is_os_junk

# Directories ignored for language stats and most file walks.
SKIP_DIR_NAMES = frozenset(
    {
        "node_modules",
        "bower_components",
        ".git",
        "dist",
        "build",
        "coverage",
        ".next",
        "__pycache__",
        "venv",
        ".venv",
        "site-packages",
        "vendor",
        "target",
        ".turbo",
        ".cache",
        "out",
        "obj",
        "Pods",
        "DerivedData",
        "xcuserdata",
        ".idea",
        ".vscode",
    }
)
# Hidden folders that still say something about the stack.
KEEP_DOT_DIRS = frozenset({".github"})

# Generated files that would drown out the code people wrote.
SKIP_FILE_NAMES = frozenset(
    {
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock", "pipfile.lock",
        "cargo.lock", "composer.lock", "gemfile.lock", "podfile.lock", "go.sum", "pubspec.lock",
    }
)
SKIP_FILE_SUFFIXES = (".min.js", ".min.css", ".map")

# Extension → language display name (for percentages).
EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "Python", ".pyw": "Python", ".ipynb": "Jupyter Notebook",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".cs": "C#", ".vb": "Visual Basic .NET", ".fs": "F#", ".fsx": "F#",
    ".go": "Go", ".rs": "Rust", ".swift": "Swift", ".m": "Objective-C", ".mm": "Objective-C++",
    ".php": "PHP", ".rb": "Ruby", ".dart": "Dart",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".ino": "C++",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "Sass", ".less": "Less",
    ".vue": "Vue", ".svelte": "Svelte", ".astro": "Astro",
    ".ejs": "EJS", ".hbs": "Handlebars", ".pug": "Pug", ".twig": "Twig", ".coffee": "CoffeeScript",
    ".sql": "SQL", ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protocol Buffer", ".prisma": "Prisma",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".fish": "Shell", ".ps1": "PowerShell", ".psm1": "PowerShell",
    ".bat": "Batchfile", ".cmd": "Batchfile",
    ".r": "R", ".rmd": "R", ".jl": "Julia", ".lua": "Lua", ".pl": "Perl", ".pm": "Perl",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell", ".clj": "Clojure", ".cljs": "Clojure",
    ".ml": "OCaml", ".elm": "Elm", ".zig": "Zig", ".nim": "Nim", ".sol": "Solidity",
    ".asm": "Assembly", ".s": "Assembly", ".f90": "Fortran", ".f": "Fortran",
    ".gd": "GDScript", ".tf": "HCL", ".v": "Verilog", ".sv": "SystemVerilog", ".vhd": "VHDL", ".vhdl": "VHDL",
    ".md": "Markdown", ".mdx": "MDX", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".xml": "XML",
}
FILENAME_TO_LANGUAGE: dict[str, str] = {
    "dockerfile": "Dockerfile", "makefile": "Makefile", "cmakelists.txt": "CMake",
    "gemfile": "Ruby", "rakefile": "Ruby", "podfile": "Ruby", "jenkinsfile": "Groovy",
}
# Only counted when a project has nothing else, like GitHub's language bar.
PROSE_AND_DATA = frozenset({"Markdown", "MDX", "JSON", "YAML", "TOML", "XML"})

# Language → tech name where they differ; None means the language is not shown as a tech.
LANGUAGE_TECH: dict[str, str | None] = {
    "Vue": "Vue.js", "SCSS": "Sass", "Jupyter Notebook": "Jupyter", "HCL": "Terraform", "GDScript": "Godot",
    "Objective-C++": "Objective-C", "Dockerfile": "Docker", "Makefile": None, "Batchfile": None,
    **{name: None for name in PROSE_AND_DATA},
}
EXT_EXTRA_TECHS: dict[str, list[str]] = {".jsx": ["React"], ".tsx": ["React"], ".ino": ["Arduino"]}

# npm package (or scope) → tech
NPM_PACKAGE_TO_TECH: dict[str, str] = {
    "react": "React", "react-dom": "React", "next": "Next.js", "vue": "Vue.js", "@vue/runtime-dom": "Vue.js",
    "nuxt": "Nuxt", "svelte": "Svelte", "@sveltejs/kit": "SvelteKit", "solid-js": "SolidJS", "astro": "Astro",
    "gatsby": "Gatsby", "@angular/core": "Angular", "react-native": "React Native", "expo": "Expo",
    "@ionic/react": "Ionic", "@ionic/angular": "Ionic", "@ionic/vue": "Ionic", "@capacitor/core": "Capacitor",
    "electron": "Electron", "@tauri-apps/api": "Tauri", "vite": "Vite", "webpack": "Webpack", "parcel": "Parcel",
    "express": "Express.js", "koa": "Koa", "fastify": "Fastify", "@hapi/hapi": "hapi", "@nestjs/core": "NestJS",
    "socket.io": "Socket.IO", "socket.io-client": "Socket.IO",
    "tailwindcss": "Tailwind CSS", "bootstrap": "Bootstrap", "react-bootstrap": "Bootstrap", "sass": "Sass",
    "styled-components": "styled-components", "@emotion/react": "Emotion", "@mui/material": "Material UI",
    "@chakra-ui/react": "Chakra UI", "antd": "Ant Design", "jquery": "jQuery", "framer-motion": "Framer Motion",
    "three": "Three.js", "d3": "D3.js", "chart.js": "Chart.js", "recharts": "Recharts", "leaflet": "Leaflet",
    "redux": "Redux", "@reduxjs/toolkit": "Redux", "zustand": "Zustand", "mobx": "MobX",
    "@tanstack/react-query": "TanStack Query", "react-query": "TanStack Query", "react-router": "React Router",
    "react-router-dom": "React Router", "axios": "Axios",
    "graphql": "GraphQL", "@apollo/client": "Apollo GraphQL", "@apollo/server": "Apollo GraphQL",
    "mongodb": "MongoDB", "mongoose": "MongoDB", "pg": "PostgreSQL", "mysql": "MySQL", "mysql2": "MySQL",
    "sqlite3": "SQLite", "better-sqlite3": "SQLite", "redis": "Redis", "ioredis": "Redis",
    "prisma": "Prisma", "@prisma/client": "Prisma", "sequelize": "Sequelize", "typeorm": "TypeORM",
    "drizzle-orm": "Drizzle", "firebase": "Firebase", "firebase-admin": "Firebase", "@supabase/supabase-js": "Supabase",
    "stripe": "Stripe", "openai": "OpenAI", "langchain": "LangChain", "@tensorflow/tfjs": "TensorFlow",
    "jest": "Jest", "vitest": "Vitest", "mocha": "Mocha", "cypress": "Cypress", "@playwright/test": "Playwright",
    "typescript": "TypeScript", "@aws-sdk/client-s3": "AWS", "aws-sdk": "AWS",
}
NPM_SCOPE_TO_TECH: dict[str, str] = {
    "@angular/": "Angular", "@nestjs/": "NestJS", "@mui/": "Material UI", "@supabase/": "Supabase",
    "@firebase/": "Firebase", "@aws-sdk/": "AWS", "@azure/": "Azure", "@remix-run/": "Remix",
    "@langchain/": "LangChain", "@testing-library/": "Testing Library", "@ionic/": "Ionic", "@trpc/": "tRPC",
}

# Python package or import name → tech
PY_TO_TECH: dict[str, str] = {
    "fastapi": "FastAPI", "django": "Django", "djangorestframework": "Django REST framework",
    "rest_framework": "Django REST framework", "flask": "Flask", "streamlit": "Streamlit", "gradio": "Gradio",
    "tensorflow": "TensorFlow", "keras": "Keras", "torch": "PyTorch", "pytorch": "PyTorch",
    "scikit-learn": "scikit-learn", "sklearn": "scikit-learn", "pandas": "pandas", "numpy": "NumPy",
    "scipy": "SciPy", "polars": "Polars", "matplotlib": "Matplotlib", "seaborn": "Seaborn", "plotly": "Plotly",
    "opencv-python": "OpenCV", "opencv-python-headless": "OpenCV", "cv2": "OpenCV",
    "transformers": "Hugging Face", "nltk": "NLTK", "spacy": "spaCy", "xgboost": "XGBoost", "lightgbm": "LightGBM",
    "langchain": "LangChain", "openai": "OpenAI", "jupyter": "Jupyter", "notebook": "Jupyter",
    "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic", "celery": "Celery", "pytest": "pytest",
    "psycopg2": "PostgreSQL", "psycopg2-binary": "PostgreSQL", "psycopg": "PostgreSQL", "asyncpg": "PostgreSQL",
    "pymysql": "MySQL", "mysqlclient": "MySQL", "mysql": "MySQL", "sqlite3": "SQLite",
    "pymongo": "MongoDB", "motor": "MongoDB", "redis": "Redis", "boto3": "AWS",
    "firebase-admin": "Firebase", "firebase_admin": "Firebase", "supabase": "Supabase", "stripe": "Stripe",
    "selenium": "Selenium", "beautifulsoup4": "Beautiful Soup", "bs4": "Beautiful Soup", "scrapy": "Scrapy",
    "pygame": "Pygame", "tkinter": "Tkinter", "pyqt5": "Qt", "pyqt6": "Qt", "pyside6": "Qt", "kivy": "Kivy",
}

SWIFT_IMPORT_TO_TECH: dict[str, str] = {
    "swiftui": "SwiftUI", "uikit": "UIKit", "coredata": "Core Data", "swiftdata": "SwiftData", "mapkit": "MapKit",
    "arkit": "ARKit", "coreml": "Core ML", "spritekit": "SpriteKit", "scenekit": "SceneKit", "realitykit": "RealityKit",
    "healthkit": "HealthKit", "combine": "Combine", "firebase": "Firebase", "firebasecore": "Firebase",
    "firebaseauth": "Firebase", "firebasefirestore": "Firebase", "alamofire": "Alamofire", "appkit": "AppKit",
}
# Java/Kotlin import prefix → tech
JVM_IMPORT_TO_TECH: list[tuple[str, str]] = [
    ("androidx.compose", "Jetpack Compose"), ("androidx.room", "Room"), ("android.", "Android"), ("androidx.", "Android"),
    ("org.springframework", "Spring Boot"), ("javafx.", "JavaFX"), ("javax.swing", "Swing"), ("java.awt", "Swing"),
    ("org.junit", "JUnit"), ("com.google.firebase", "Firebase"), ("retrofit2", "Retrofit"), ("io.ktor", "Ktor"),
    ("org.hibernate", "Hibernate"), ("jakarta.persistence", "Hibernate"), ("javax.persistence", "Hibernate"),
]
# C# using prefix → tech
CS_USING_TO_TECH: list[tuple[str, str]] = [
    ("UnityEngine", "Unity"), ("Microsoft.AspNetCore", "ASP.NET"), ("Microsoft.EntityFrameworkCore", "Entity Framework"),
    ("System.Windows.Forms", "Windows Forms"), ("System.Windows", "WPF"), ("Microsoft.Maui", ".NET MAUI"),
    ("Xamarin", "Xamarin"), ("Xunit", "xUnit"), ("NUnit", "NUnit"), ("Godot", "Godot"),
]
DART_PACKAGE_TO_TECH: dict[str, str] = {
    "flutter": "Flutter", "firebase_core": "Firebase", "cloud_firestore": "Firebase", "firebase_auth": "Firebase",
    "provider": "Provider", "flutter_riverpod": "Riverpod", "riverpod": "Riverpod", "flutter_bloc": "Bloc",
    "get": "GetX", "dio": "Dio", "sqflite": "SQLite", "supabase_flutter": "Supabase",
}
# Libraries pulled from a CDN in plain HTML projects (matched inside script/link URLs).
CDN_TO_TECH: list[tuple[str, str]] = [
    ("bootstrap", "Bootstrap"), ("jquery", "jQuery"), ("tailwindcss", "Tailwind CSS"), ("chart.js", "Chart.js"),
    ("font-awesome", "Font Awesome"), ("fontawesome", "Font Awesome"), ("leaflet", "Leaflet"), ("three", "Three.js"),
    ("d3.v", "D3.js"), ("/d3@", "D3.js"), ("vue@", "Vue.js"), ("unpkg.com/react@", "React"), ("alpinejs", "Alpine.js"),
    ("htmx", "htmx"), ("gsap", "GSAP"), ("firebase", "Firebase"), ("socket.io", "Socket.IO"),
]
GO_MODULE_TO_TECH: list[tuple[str, str]] = [
    ("github.com/gin-gonic/gin", "Gin"), ("github.com/labstack/echo", "Echo"), ("github.com/gofiber/fiber", "Fiber"),
    ("gorm.io/gorm", "GORM"), ("github.com/gorilla/mux", "Gorilla Mux"), ("github.com/go-chi/chi", "Chi"),
    ("github.com/jackc/pgx", "PostgreSQL"), ("github.com/lib/pq", "PostgreSQL"), ("go.mongodb.org", "MongoDB"),
    ("github.com/redis/go-redis", "Redis"), ("github.com/go-redis/redis", "Redis"),
]
RUST_CRATE_TO_TECH: dict[str, str] = {
    "actix-web": "Actix Web", "axum": "Axum", "rocket": "Rocket", "tokio": "Tokio", "tauri": "Tauri", "bevy": "Bevy",
    "diesel": "Diesel", "sqlx": "SQLx", "serde": "Serde", "yew": "Yew", "leptos": "Leptos",
}
RUBY_GEM_TO_TECH: dict[str, str] = {
    "rails": "Ruby on Rails", "sinatra": "Sinatra", "rspec": "RSpec", "rspec-rails": "RSpec", "pg": "PostgreSQL",
    "mysql2": "MySQL", "sqlite3": "SQLite", "redis": "Redis", "sidekiq": "Sidekiq", "devise": "Devise",
}
PHP_PACKAGE_TO_TECH: list[tuple[str, str]] = [
    ("laravel/framework", "Laravel"), ("symfony/", "Symfony"), ("slim/slim", "Slim"), ("phpunit/phpunit", "PHPUnit"),
    ("codeigniter4/", "CodeIgniter"), ("cakephp/cakephp", "CakePHP"), ("livewire/livewire", "Livewire"),
]
# Text inside JVM / .NET build files → tech
JVM_BUILD_TO_TECH: list[tuple[str, str]] = [
    ("com.android.application", "Android"), ("com.android.library", "Android"), ("androidx.compose", "Jetpack Compose"),
    ("spring-boot", "Spring Boot"), ("springframework.boot", "Spring Boot"), ("hibernate", "Hibernate"),
    ("junit", "JUnit"), ("firebase", "Firebase"), ("io.ktor", "Ktor"), ("androidx.room", "Room"),
    ("retrofit", "Retrofit"), ("javafx", "JavaFX"), ("mysql", "MySQL"), ("postgresql", "PostgreSQL"),
]
DOTNET_PROJECT_TO_TECH: list[tuple[str, str]] = [
    ("microsoft.net.sdk.web", "ASP.NET"), ("microsoft.aspnetcore", "ASP.NET"),
    ("entityframeworkcore", "Entity Framework"), ("<usewpf>true", "WPF"), ("<usewindowsforms>true", "Windows Forms"),
    ("<usemaui>true", ".NET MAUI"), ("xamarin", "Xamarin"), ("xunit", "xUnit"), ("nunit", "NUnit"),
    ("microsoft.net.sdk", ".NET"),
]

# Exact file name → tech
MARKER_FILES: dict[str, list[str]] = {
    "dockerfile": ["Docker"], "docker-compose.yml": ["Docker"], "docker-compose.yaml": ["Docker"],
    "compose.yml": ["Docker"], "compose.yaml": ["Docker"], "jenkinsfile": ["Jenkins", "CI/CD"],
    ".gitlab-ci.yml": ["GitLab CI", "CI/CD"], ".travis.yml": ["Travis CI", "CI/CD"],
    "azure-pipelines.yml": ["Azure Pipelines", "CI/CD"], "bitbucket-pipelines.yml": ["Bitbucket Pipelines", "CI/CD"],
    "vercel.json": ["Vercel"], "netlify.toml": ["Netlify"], "firebase.json": ["Firebase"], "serverless.yml": ["Serverless Framework"],
    "androidmanifest.xml": ["Android"], "project.godot": ["Godot"], "cmakelists.txt": ["CMake"],
    "platformio.ini": ["PlatformIO"], "podfile": ["CocoaPods"], "package.swift": ["Swift Package Manager"],
    "angular.json": ["Angular"], "tsconfig.json": ["TypeScript"], "pom.xml": ["Maven"], "build.gradle": ["Gradle"],
    "build.gradle.kts": ["Gradle"], "settings.gradle": ["Gradle"], "manage.py": ["Django"], "artisan": ["Laravel"],
    "wp-config.php": ["WordPress"], "projectversion.txt": ["Unity"],
}
# File name prefix (config files with varying extensions) → tech
MARKER_PREFIXES: list[tuple[str, str]] = [
    ("dockerfile.", "Docker"), ("vite.config.", "Vite"), ("next.config.", "Next.js"), ("nuxt.config.", "Nuxt"),
    ("svelte.config.", "Svelte"), ("astro.config.", "Astro"), ("tailwind.config.", "Tailwind CSS"),
    ("webpack.config.", "Webpack"), ("jest.config.", "Jest"), ("vitest.config.", "Vitest"),
    ("playwright.config.", "Playwright"), ("cypress.config.", "Cypress"),
]

# Map alternate detected names onto catalog names.
CATALOG_ALIASES: dict[str, str] = {
    "vue": "Vue.js", "vuejs": "Vue.js", "express": "Express.js", "expressjs": "Express.js", "next": "Next.js",
    "nextjs": "Next.js", "tailwind": "Tailwind CSS", "tailwindcss": "Tailwind CSS", "fastapi": "FastAPI",
    "nodejs": "Node.js", "node": "Node.js", "spring": "Spring Boot", "springboot": "Spring Boot",
    "reactnative": "React Native", "aspnet": "ASP.NET", "aspnetcore": "ASP.NET",
}

SCANNED_SOURCE_LIMIT = 3_000
SCAN_BYTES = 128_000
PY_IMPORT = re.compile(r'(?m)^[\s"]*(?:from|import)\s+([A-Za-z_]\w*)')
JS_IMPORT = re.compile(r"""(?:\bfrom\s*|\brequire\(\s*|\bimport\s*\(?\s*)['"]([^'"./][^'"]*)['"]""")
SWIFT_IMPORT = re.compile(r"(?m)^\s*(?:@testable\s+)?import\s+(\w+)")
JVM_IMPORT = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+)")
CS_USING = re.compile(r"(?m)^\s*(?:global\s+)?using\s+(?:static\s+)?([\w.]+)\s*;")
DART_IMPORT = re.compile(r"""import\s+['"]package:(\w+)/""")
HTML_URL = re.compile(r"""(?:src|href)\s*=\s*['"]([^'"]+)['"]""", re.I)
K8S_KIND = re.compile(r"(?m)^kind:\s*(Deployment|Service|Ingress|StatefulSet|DaemonSet|Pod|ConfigMap)\b")


EVIDENCE_PER_KIND = 3


@dataclass
class AnalysisResult:
    languages: list[dict] = field(default_factory=list)
    tech_names: list[str] = field(default_factory=list)
    # Why each tech was detected: {"React": [{"kind": "dependency", "file": "package.json"}, ...]}.
    # kind is extension, dependency, config or import; a few files per kind, paths relative to the workspace.
    evidence: dict[str, list[dict]] = field(default_factory=dict)


def _should_skip_dir(name: str) -> bool:
    if name in KEEP_DOT_DIRS:
        return False
    return name in SKIP_DIR_NAMES or name.startswith(".") or is_os_junk(name)


def _skip_file(name: str) -> bool:
    lower = name.lower()
    return is_os_junk(name) or lower in SKIP_FILE_NAMES or lower.endswith(SKIP_FILE_SUFFIXES)


def _iter_source_files(root: Path):
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune vendor / generated dirs in-place.
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        base = Path(dirpath)
        for fname in filenames:
            path = base / fname
            if _skip_file(fname) or path.is_symlink():
                continue
            yield path


def _canon(name: str) -> str:
    return CATALOG_ALIASES.get(re.sub(r"[^a-z0-9]+", "", name.lower()), name)


def _language_of(path: Path) -> str | None:
    if path.suffix.lower() == ".m":
        objc = re.search(r"#import|@interface|@implementation", _read_text_capped(path, 8192))
        return "Objective-C" if objc else "MATLAB"
    return FILENAME_TO_LANGUAGE.get(path.name.lower()) or EXT_TO_LANGUAGE.get(path.suffix.lower())


def analyze_languages(workspace_root: Path, files: list[Path] | None = None) -> list[dict]:
    """Byte-weighted language percentages (0–100, one decimal)."""
    totals: dict[str, int] = {}
    for path in files if files is not None else _iter_source_files(workspace_root):
        lang = _language_of(path)
        if not lang:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 0:
            totals[lang] = totals.get(lang, 0) + size

    if any(lang not in PROSE_AND_DATA for lang in totals):
        totals = {k: v for k, v in totals.items() if k not in PROSE_AND_DATA}
    grand = sum(totals.values())
    if grand <= 0:
        return []

    rows = []
    for name, nbytes in totals.items():
        pct = round((nbytes / grand) * 1000) / 10  # one decimal
        rows.append({"name": name, "percentage": pct})
    rows.sort(key=lambda r: (-r["percentage"], r["name"]))

    # Re-normalize so percentages sum ~100 after rounding.
    drift = round(1000 - sum(int(r["percentage"] * 10) for r in rows)) / 10
    rows[0]["percentage"] = round((rows[0]["percentage"] + drift) * 10) / 10
    return rows


def _read_text_capped(path: Path, limit: int = 512_000) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _prefix_hits(text: str, rules: list[tuple[str, str]]) -> set[str]:
    return {tech for needle, tech in rules if needle in text}


def _npm_tech(package: str) -> str | None:
    pkg = package.lower()
    if pkg.startswith("@"):
        pkg = "/".join(pkg.split("/")[:2])
    else:
        pkg = pkg.split("/")[0]
    if pkg in NPM_PACKAGE_TO_TECH:
        return NPM_PACKAGE_TO_TECH[pkg]
    return next((tech for scope, tech in NPM_SCOPE_TO_TECH.items() if pkg.startswith(scope)), None)


def _py_tech(name: str) -> str | None:
    return PY_TO_TECH.get(name.lower().replace("_", "-")) or PY_TO_TECH.get(name.lower())


def _dependency_names(text: str) -> set[str]:
    """Package names from TOML/YAML/setup files: `name = ...` keys, quoted specs, and `- name` list items."""
    names = re.findall(r"(?m)^\s*([A-Za-z0-9_.\-]+)\s*=", text)
    names += re.findall(r"""['"]([A-Za-z0-9_.\-]+)\s*(?:[<>=!~\[;]|['"])""", text)
    names += re.findall(r"(?m)^\s*-\s*([A-Za-z0-9_.\-]+)", text)
    return {n.lower() for n in names}


def _techs_from_extensions(files: list[Path]) -> set[str]:
    found: set[str] = set()
    for path in files:
        lang = _language_of(path)
        if lang:
            tech = LANGUAGE_TECH.get(lang, lang)
            if tech:
                found.add(tech)
        found.update(EXT_EXTRA_TECHS.get(path.suffix.lower(), []))
    return found


def _techs_from_package_json(path: Path) -> set[str]:
    try:
        data = json.loads(_read_text_capped(path))
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, dict):
        return set()
    deps: dict = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        block = data.get(key) or {}
        if isinstance(block, dict):
            deps.update(block)
    found = {"Node.js"} if deps else set()
    found |= {tech for pkg in deps if (tech := _npm_tech(pkg))}
    return found


def _techs_from_requirements(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Strip extras / version pins: fastapi[standard]==0.1 → fastapi
        token = re.split(r"[<>=!~\[;\s]", line, maxsplit=1)[0].strip("\"' ")
        if token and (tech := _py_tech(token)):
            found.add(tech)
    return found


def _techs_from_manifest(path: Path) -> set[str]:
    """Dependency and build files of every ecosystem."""
    name = path.name.lower()
    if name == "package.json":
        return _techs_from_package_json(path)
    if name.startswith("requirements") and name.endswith(".txt"):
        return _techs_from_requirements(_read_text_capped(path))
    if name in ("pipfile", "pyproject.toml", "setup.py", "setup.cfg", "environment.yml", "environment.yaml"):
        return {tech for dep in _dependency_names(_read_text_capped(path)) if (tech := _py_tech(dep))}
    if name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        return _prefix_hits(_read_text_capped(path).lower(), JVM_BUILD_TO_TECH)
    if path.suffix.lower() in (".csproj", ".fsproj", ".vbproj"):
        return _prefix_hits(re.sub(r"\s+", "", _read_text_capped(path).lower()), DOTNET_PROJECT_TO_TECH) | {".NET"}
    if name == "go.mod":
        return _prefix_hits(_read_text_capped(path), GO_MODULE_TO_TECH)
    if name == "cargo.toml":
        return {RUST_CRATE_TO_TECH[dep] for dep in _dependency_names(_read_text_capped(path)) if dep in RUST_CRATE_TO_TECH}
    if name == "gemfile":
        gems = re.findall(r"""^\s*gem\s+['"]([^'"]+)['"]""", _read_text_capped(path), re.M)
        return {RUBY_GEM_TO_TECH[g.lower()] for g in gems if g.lower() in RUBY_GEM_TO_TECH}
    if name == "composer.json":
        return _prefix_hits(_read_text_capped(path).lower(), PHP_PACKAGE_TO_TECH)
    if name == "pubspec.yaml":
        text = _read_text_capped(path)
        deps = re.findall(r"(?m)^ {2}([a-z0-9_]+):", text)
        found = {DART_PACKAGE_TO_TECH[d] for d in deps if d in DART_PACKAGE_TO_TECH and d != "flutter"}
        return found | ({"Flutter"} if re.search(r"sdk:\s*flutter", text) else set())
    return set()


def _techs_from_markers(path: Path) -> set[str]:
    """Config files and folders whose presence names a tool."""
    name = path.name.lower()
    found = set(MARKER_FILES.get(name, []))
    found |= {tech for prefix, tech in MARKER_PREFIXES if name.startswith(prefix)}
    parts = [p.lower() for p in path.parts]
    if any(p.endswith((".xcodeproj", ".xcworkspace")) for p in parts):
        found.add("Xcode")
    if ".github" in parts and "workflows" in parts:
        found |= {"GitHub Actions", "CI/CD"}
    if "wp-content" in parts:
        found.add("WordPress")
    return found


def _notebook_code(path: Path) -> str:
    """Source of a notebook's cells; outputs (often large images) are skipped."""
    try:
        cells = json.loads(_read_text_capped(path, 8_000_000)).get("cells", [])
    except (json.JSONDecodeError, AttributeError):
        return ""
    lines: list[str] = []
    for cell in cells if isinstance(cells, list) else []:
        source = cell.get("source", []) if isinstance(cell, dict) else []
        lines.append("".join(source) if isinstance(source, list) else str(source))
    return "\n".join(lines)


def _techs_from_source(path: Path) -> set[str]:
    """Imports and includes inside the code itself, so projects without manifests still show their libraries."""
    ext = path.suffix.lower()
    if ext in (".py", ".pyw", ".ipynb"):
        code = _notebook_code(path) if ext == ".ipynb" else _read_text_capped(path, SCAN_BYTES)
        return {tech for mod in PY_IMPORT.findall(code) if (tech := _py_tech(mod))}
    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro"):
        return {tech for pkg in JS_IMPORT.findall(_read_text_capped(path, SCAN_BYTES)) if (tech := _npm_tech(pkg))}
    if ext == ".swift":
        mods = SWIFT_IMPORT.findall(_read_text_capped(path, SCAN_BYTES))
        return {SWIFT_IMPORT_TO_TECH[m.lower()] for m in mods if m.lower() in SWIFT_IMPORT_TO_TECH}
    if ext in (".java", ".kt", ".kts"):
        imports = JVM_IMPORT.findall(_read_text_capped(path, SCAN_BYTES))
        return {tech for imp in imports for prefix, tech in JVM_IMPORT_TO_TECH if imp.startswith(prefix)}
    if ext == ".cs":
        usings = CS_USING.findall(_read_text_capped(path, SCAN_BYTES))
        return {tech for u in usings for prefix, tech in CS_USING_TO_TECH if u.startswith(prefix)}
    if ext == ".dart":
        pkgs = DART_IMPORT.findall(_read_text_capped(path, SCAN_BYTES))
        return {DART_PACKAGE_TO_TECH[p] for p in pkgs if p in DART_PACKAGE_TO_TECH}
    if ext in (".html", ".htm", ".php", ".ejs", ".hbs"):
        urls = " ".join(HTML_URL.findall(_read_text_capped(path, SCAN_BYTES))).lower()
        return _prefix_hits(urls, CDN_TO_TECH)
    if ext in (".yml", ".yaml"):
        text = _read_text_capped(path, SCAN_BYTES)
        return {"Kubernetes"} if "apiVersion:" in text and K8S_KIND.search(text) else set()
    return set()


def analyze_workspace(workspace_root: Path) -> AnalysisResult:
    """Full analysis: language percentages, merged tech names and the files behind each tech."""
    root = workspace_root.resolve()
    files = list(_iter_source_files(root))
    evidence: dict[str, list[dict]] = {}

    def note(techs: set[str], kind: str, path: Path) -> None:
        for tech in techs:
            rows = evidence.setdefault(_canon(tech), [])
            if sum(r["kind"] == kind for r in rows) < EVIDENCE_PER_KIND:
                rows.append({"kind": kind, "file": path.relative_to(root).as_posix()})

    for i, path in enumerate(files):
        note(_techs_from_extensions([path]), "extension", path)
        note(_techs_from_manifest(path), "dependency", path)
        note(_techs_from_markers(path), "config", path)
        if i < SCANNED_SOURCE_LIMIT:
            note(_techs_from_source(path), "import", path)
    return AnalysisResult(languages=analyze_languages(root, files), tech_names=sorted(evidence), evidence=evidence)


def resolve_tech_stacks_from_names(db, names: list[str]):
    """
    Map detected names to TechStack rows, adding any the catalog does not have yet.
    Returns (linked_rows, linked_names, linked_ids).
    """
    from sqlalchemy import func
    from sqlalchemy.exc import IntegrityError

    from app.models.techstack import TechStack

    if not names:
        return [], [], []

    by_lower = {t.name.lower(): t for t in db.query(TechStack).all()}
    linked = []
    seen: set[int] = set()
    for name in names:
        tech = by_lower.get(name.lower())
        if tech is None:
            try:
                with db.begin_nested():
                    tech = TechStack(name=name)
                    db.add(tech)
            except IntegrityError:
                # Another upload added it a moment ago.
                tech = db.query(TechStack).filter(func.lower(TechStack.name) == name.lower()).first()
            if tech is None:
                continue
            by_lower[name.lower()] = tech
        tid = int(tech.id)
        if tid in seen:
            continue
        seen.add(tid)
        linked.append(tech)
    return linked, [t.name for t in linked], [int(t.id) for t in linked]
