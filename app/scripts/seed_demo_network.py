import uuid
from datetime import datetime, timedelta
from app.db.session import SessionLocal, engine
from app.db.base import (
    Base,
    User,
    UserProfile,
    Project,
    TechStack,
    File,
    NetworkActivity,
    ActivityLike,
    ActivityComment,
    Connection,
    Message,
    CollaborationRequest,
    ProjectCollaborator,
    Notification,
)
from app.models.enums import ProjectCategory, ProjectVisibility
from app.core.security import hash_password

DEMO_USERS = [
    {
        "username": "janasi",
        "email": "janasi@devzgo.com",
        "full_name": "Janasi Rajput",
        "headline": "Backend & Network Lead | Distributed Systems",
        "bio": "Building real-time developer networking, peer messaging, and collaboration infrastructure for DevZ-Go.",
        "skills": "Python, FastAPI, PostgreSQL, Redis, WebSockets, Docker, Microservices",
        "github_url": "https://github.com/janasirajput",
        "location": "New York, USA",
        "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=256&h=256&fit=crop&crop=faces",
    },
    {
        "username": "chan",
        "email": "chan@devzgo.com",
        "full_name": "Chan S.",
        "headline": "Authentication & Project Architect",
        "bio": "Leading authentication, secure token vaults, and static code intelligence detection algorithms.",
        "skills": "React, FastAPI, Docker, OAuth2, AST Analysis, TypeScript, TailwindCSS",
        "github_url": "https://github.com/chan-dev",
        "location": "San Francisco, USA",
        "avatar_url": "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=256&h=256&fit=crop&crop=faces",
    },
    {
        "username": "khushbu",
        "email": "khushbu@devzgo.com",
        "full_name": "Khushbu Patel",
        "headline": "Recruiter & Talent Discovery Specialist",
        "bio": "Connecting world-class engineering talent with modern tech teams through open proof of work.",
        "skills": "React, TypeScript, Talent Tech, GraphQL, UI/UX Design, Algolia",
        "github_url": "https://github.com/khushbu-dev",
        "location": "Toronto, Canada",
        "avatar_url": "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=256&h=256&fit=crop&crop=faces",
    },
    {
        "username": "manasi",
        "email": "manasi@devzgo.com",
        "full_name": "Manasi Sharma",
        "headline": "Analytics & Dashboard Engineer",
        "bio": "Crafting developer metrics, monthly wrap-ups, and productivity insights from raw git signals.",
        "skills": "Python, Data Visualization, Next.js, Pandas, Chart.js, TailwindCSS",
        "github_url": "https://github.com/manasi-eng",
        "location": "London, UK",
        "avatar_url": "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=256&h=256&fit=crop&crop=faces",
    },
]

def seed_demo_data():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user_map = {}

        # 1. Create or fetch demo users and profiles
        for uinfo in DEMO_USERS:
            user = db.query(User).filter(User.email == uinfo["email"]).first()
            if not user:
                user = User(
                    username=uinfo["username"],
                    email=uinfo["email"],
                    password_hash=hash_password("DevzgoDemo123!"),
                )
                db.add(user)
                db.commit()
                db.refresh(user)

            # Profile
            profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
            if not profile:
                profile = UserProfile(
                    user_id=user.id,
                    full_name=uinfo["full_name"],
                    headline=uinfo["headline"],
                    bio=uinfo["bio"],
                    skills=uinfo["skills"],
                    github_url=uinfo["github_url"],
                    location=uinfo["location"],
                    avatar_url=uinfo["avatar_url"],
                )
                db.add(profile)
                db.commit()
            user_map[uinfo["username"]] = user

        # Fetch tech stacks
        tech_map = {t.name: t for t in db.query(TechStack).all()}

        # 2. Create demo projects if not existing
        projects_data = [
            {
                "owner": user_map["chan"],
                "title": "DevZ-Go Core Platform",
                "short_description": "Unified developer portfolio and collaborative proof-of-work ecosystem.",
                "full_description": "DevZ-Go connects developer profiles, automated AST code analysis, real-time peer messaging, and team recruitment into a cohesive platform.",
                "category": ProjectCategory.WEB_DEVELOPMENT,
                "visibility": ProjectVisibility.PUBLIC,
                "cover_image_url": "https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=1200&h=630&fit=crop",
                "github_url": "https://github.com/DevZ-Go",
                "complexity": "Advanced",
                "contribution_info": "Architecture, Auth, and File Tree Parsing",
                "techs": ["Python", "FastAPI", "React", "TypeScript", "Docker"],
            },
            {
                "owner": user_map["janasi"],
                "title": "AI Resume & Portfolio Analyzer",
                "short_description": "Extracts skills, project complexity, and engineering depth from student repositories.",
                "full_description": "Parses commit histories, detects programming languages and framework idioms, generating verifiable skill badges.",
                "category": ProjectCategory.AI_ML,
                "visibility": ProjectVisibility.PUBLIC,
                "cover_image_url": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=1200&h=630&fit=crop",
                "github_url": "https://github.com/janasirajput/ai-resume-analyzer",
                "complexity": "Advanced",
                "contribution_info": "NLP Extraction, Graph-based skill scoring, REST API",
                "techs": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            },
            {
                "owner": user_map["khushbu"],
                "title": "DevHunt — Talent Discovery Engine",
                "short_description": "High-velocity technical recruiter search engine with verified proof-of-work filters.",
                "full_description": "Provides instant search across developer skills, github commit milestones, and project complexity ratings.",
                "category": ProjectCategory.WEB_DEVELOPMENT,
                "visibility": ProjectVisibility.PUBLIC,
                "cover_image_url": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=1200&h=630&fit=crop",
                "github_url": "https://github.com/khushbu-dev/devhunt",
                "complexity": "Intermediate",
                "contribution_info": "Elasticsearch indices, React client, faceted filtering",
                "techs": ["React", "TypeScript", "GraphQL"],
            },
            {
                "owner": user_map["manasi"],
                "title": "Metrics & Engineering Wrap-up",
                "short_description": "Interactive developer productivity dashboards and monthly GitHub activity recaps.",
                "full_description": "Aggregates lines shipped, PR reviews, network collaborations, and badge achievements into exportable reports.",
                "category": ProjectCategory.PRODUCTIVITY,
                "visibility": ProjectVisibility.PUBLIC,
                "cover_image_url": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=1200&h=630&fit=crop",
                "github_url": "https://github.com/manasi-eng/metrics-wrapup",
                "complexity": "Intermediate",
                "contribution_info": "Chart.js components, aggregation endpoints, PDF generation",
                "techs": ["Python", "Next.js", "PostgreSQL"],
            },
        ]

        proj_map = {}
        for pdata in projects_data:
            proj = db.query(Project).filter(Project.title == pdata["title"]).first()
            if not proj:
                proj = Project(
                    owner_id=pdata["owner"].id,
                    title=pdata["title"],
                    short_description=pdata["short_description"],
                    full_description=pdata["full_description"],
                    category=pdata["category"],
                    visibility=pdata["visibility"],
                    cover_image_url=pdata["cover_image_url"],
                    github_url=pdata["github_url"],
                    complexity=pdata["complexity"],
                    contribution_info=pdata["contribution_info"],
                )
                for tname in pdata["techs"]:
                    if tname in tech_map:
                        proj.tech_stacks.append(tech_map[tname])
                db.add(proj)
                db.commit()
                db.refresh(proj)
            proj_map[pdata["title"]] = proj

        # 3. Add Project Collaborators to DevZ-Go Core Platform
        core_proj = proj_map.get("DevZ-Go Core Platform")
        if core_proj:
            collabs = [
                (user_map["janasi"], "Backend Developer"),
                (user_map["chan"], "Authentication & Security"),
                (user_map["khushbu"], "Recruiter & Explore"),
                (user_map["manasi"], "Analytics & Insights"),
            ]
            for u, role in collabs:
                exists = db.query(ProjectCollaborator).filter(
                    ProjectCollaborator.project_id == core_proj.id,
                    ProjectCollaborator.user_id == u.id,
                ).first()
                if not exists:
                    db.add(ProjectCollaborator(project_id=core_proj.id, user_id=u.id, role=role))
            db.commit()

        # 4. Connections between team members (accepted)
        team_pairs = [
            (user_map["janasi"], user_map["chan"]),
            (user_map["janasi"], user_map["khushbu"]),
            (user_map["janasi"], user_map["manasi"]),
            (user_map["chan"], user_map["khushbu"]),
            (user_map["chan"], user_map["manasi"]),
        ]
        for u1, u2 in team_pairs:
            existing = db.query(Connection).filter(
                (Connection.requester_id == u1.id) & (Connection.receiver_id == u2.id)
                | (Connection.requester_id == u2.id) & (Connection.receiver_id == u1.id)
            ).first()
            if not existing:
                db.add(Connection(requester_id=u1.id, receiver_id=u2.id, status="accepted"))
        db.commit()

        # 5. Network Activities (marked is_demo=True)
        activities_data = [
            {
                "user": user_map["janasi"],
                "type": "project_added",
                "project": proj_map.get("AI Resume & Portfolio Analyzer"),
                "title": "Janasi Rajput added a new project",
                "content": "Published AI Resume Analyzer! It analyzes repository commit cadence, technical debt, and extracts verified engineering skills automatically.",
                "days_ago": 1,
            },
            {
                "user": user_map["chan"],
                "type": "project_analyzed",
                "project": proj_map.get("DevZ-Go Core Platform"),
                "title": "Chan analyzed a project",
                "content": "Completed static security audit and technology detection on DevZ-Go Core Platform. Verified clean dependencies and modular services.",
                "days_ago": 2,
            },
            {
                "user": user_map["khushbu"],
                "type": "profile_updated",
                "project": None,
                "title": "Khushbu updated their developer profile",
                "content": "Updated developer portfolio with new talent matching benchmarks and developer verification filters.",
                "days_ago": 3,
            },
            {
                "user": user_map["manasi"],
                "type": "wrap_up_published",
                "project": proj_map.get("Metrics & Engineering Wrap-up"),
                "title": "Manasi published a monthly wrap-up",
                "content": "Our team logged 2,400+ lines of clean TypeScript, 18 PRs, and 4 microservices this month! Check out the engineering wrap-up breakdown.",
                "days_ago": 4,
            },
            {
                "user": user_map["janasi"],
                "type": "collaboration",
                "project": proj_map.get("DevZ-Go Core Platform"),
                "title": "Janasi Rajput joined as Backend Developer",
                "content": "Excited to collaborate on DevZ-Go Core Platform with @chan, @khushbu, and @manasi to build the next-generation developer network!",
                "days_ago": 5,
            },
        ]

        created_activities = []
        for act in activities_data:
            existing_act = db.query(NetworkActivity).filter(
                NetworkActivity.user_id == act["user"].id,
                NetworkActivity.title == act["title"],
            ).first()
            if not existing_act:
                dt = datetime.utcnow() - timedelta(days=act["days_ago"])
                new_act = NetworkActivity(
                    user_id=act["user"].id,
                    activity_type=act["type"],
                    project_id=act["project"].id if act["project"] else None,
                    title=act["title"],
                    content=act["content"],
                    is_demo=True,
                    created_at=dt,
                )
                db.add(new_act)
                db.commit()
                db.refresh(new_act)
                created_activities.append(new_act)
            else:
                created_activities.append(existing_act)

        # 6. Add Likes & Comments on activities
        if created_activities:
            first_act = created_activities[0]
            # Like by Chan & Khushbu
            for u in [user_map["chan"], user_map["khushbu"]]:
                has_like = db.query(ActivityLike).filter(
                    ActivityLike.activity_id == first_act.id,
                    ActivityLike.user_id == u.id,
                ).first()
                if not has_like:
                    db.add(ActivityLike(activity_id=first_act.id, user_id=u.id))

            # Comments
            comments_data = [
                (user_map["chan"], "Super clean architecture! The automated skill detection looks very impressive."),
                (user_map["khushbu"], "This makes technical recruiter screening 10x faster. Great work Janasi!"),
            ]
            for u, ctext in comments_data:
                has_comm = db.query(ActivityComment).filter(
                    ActivityComment.activity_id == first_act.id,
                    ActivityComment.author_id == u.id,
                ).first()
                if not has_comm:
                    db.add(ActivityComment(activity_id=first_act.id, author_id=u.id, comment_text=ctext))

            db.commit()

        # 7. Messages between Chan and Janasi
        demo_msgs = [
            (user_map["chan"], user_map["janasi"], "Hey Janasi! How is the Network feed and messaging architecture coming along?"),
            (user_map["janasi"], user_map["chan"], "Hey Chan! The data models and endpoints are fully connected to your projects and auth tables."),
            (user_map["chan"], user_map["janasi"], "Awesome, that will make the collaboration flow seamless!"),
        ]
        for sender, receiver, text in demo_msgs:
            exists_msg = db.query(Message).filter(
                Message.sender_id == sender.id,
                Message.receiver_id == receiver.id,
                Message.content == text,
            ).first()
            if not exists_msg:
                db.add(Message(sender_id=sender.id, receiver_id=receiver.id, content=text, read=True))
        db.commit()

        # 8. Demo Notifications for Janasi
        demo_notifs = [
            (user_map["chan"], user_map["janasi"], "like", "Chan liked your AI Resume Analyzer activity."),
            (user_map["khushbu"], user_map["janasi"], "comment", "Khushbu commented on your activity: \"This makes technical recruiter screening 10x faster...\""),
            (user_map["manasi"], user_map["janasi"], "connection_accepted", "Manasi accepted your connection request."),
        ]
        for actor, recip, ntype, nmsg in demo_notifs:
            exists_n = db.query(Notification).filter(
                Notification.recipient_id == recip.id,
                Notification.actor_id == actor.id,
                Notification.type == ntype,
            ).first()
            if not exists_n:
                db.add(Notification(
                    recipient_id=recip.id,
                    actor_id=actor.id,
                    type=ntype,
                    message=nmsg,
                    read=False,
                ))
        db.commit()

        print("Demo seed completed successfully for Chan, Janasi, Khushbu, and Manasi.")
    finally:
        db.close()

if __name__ == "__main__":
    seed_demo_data()
