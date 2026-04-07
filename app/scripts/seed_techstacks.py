from app.db import base
from app.db.session import SessionLocal
from app.models.techstack import TechStack

tech_names = [
    "Python",
    "JavaScript",
    "HTML",
    "CSS",
    "JSON",
    "Java",
    "C#",
    "Go",
    "PHP",
    "Swift",
    "Kotlin",
    "TypeScript",
    "React",
    "Angular",
    "Vue.js",
    "Django",
    "Flask",
    "Spring Boot",
    "ASP.NET",
    "Express.js",
    "Laravel",
    "TensorFlow",
    "Docker",
    "Kubernetes",
    "AWS",
    "Azure",
    "PostgreSQL",
    "MySQL",
    "MongoDB",
    "Redis",
    "GraphQL",
    "REST",
    "Git",
    "CI/CD",
    "Flutter",
    "React Native",
    "Ionic",
    "Node.js",
    "Next.js"
]

def seed():
    db = SessionLocal()

    for name in tech_names:
        existsing = db.query(TechStack).filter(TechStack.name == name).first()
        if not existsing:
            tech_stack = TechStack(name=name)
            db.add(tech_stack)
    db.commit()
    db.close()
    print("Tech stacks seeded successfully.")

if __name__ == "__main__":
    seed()