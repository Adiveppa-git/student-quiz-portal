# 📝 Student Quiz Portal

🚀 Student Quiz Portal is a full-stack web application designed to conduct secure and interactive online quizzes for students.

It supports both **Admin** and **Student** roles, allowing admins to manage quizzes and students to attempt tests with instant score evaluation.

This project demonstrates real-world concepts like authentication, database integration, session management, role-based access, and responsive UI design.

---


# 🖼️ Application Preview

# 🖼️ Application Preview

## 🔐 Login Page
![Login Page](screenshots/login.png)

## 👨‍💼 Admin Dashboard
![Admin Dashboard](screenshots/admin-dash.png)

## 👨‍🎓 Student Dashboard
![Student Dashboard](screenshots/student-dash.png)

## ❓ Manage Students
![Manage Students](screenshots/manage-stu.png)

## 📝 Manage Quizzes
![Manage Quizzes](screenshots/manage-quizes.png)

## ❓ Manage Questions
![Manage Questions](screenshots/manage-questions.png)

## 📝 Quiz Interface
![Quiz Interface](screenshots/attending-quiz.png)

## 📊 Result Section
![Result Section](screenshots/result.png)

## 🏆 Leaderboard
![Leaderboard](screenshots/leaderboard.png)

## 📜 Quiz History
![Quiz History](screenshots/history.png)

## 📝 Register Page
![Register Page](screenshots/register.png)

---

# ✨ Core Features

## 👤 Student Features
- 📝 Attempt quizzes online
- ⏱ Timer-based quiz system
- 📊 Instant score calculation
- 🏆 Leaderboard ranking
- 📈 Performance tracking

## 👑 Admin Features
- 🧑‍💼 Admin login system
- ➕ Add quiz questions
- ✏️ Edit & manage questions
- ❌ Delete questions
- 📊 Monitor student performance

## 🔐 Authentication & Security
- Secure login system
- Session management
- Role-based access control
- Input validation

## Quiz Availability Timezone
Quiz scheduling values are stored and compared as UTC. Administrator inputs labeled
`Available From` and `Available Until` must be entered in UTC. MySQL schedule
checks use `UTC_TIMESTAMP()` so server-side availability does not depend on browser
time zones. The existing per-attempt quiz time limit remains separate from quiz
scheduling availability.

---

# 🧩 Technology Stack

## 💻 Frontend
- HTML
- CSS
- JavaScript

## ⚙️ Backend
- Python Flask

## 🗄️ Database
- MySQL

## ☁️ Deployment
- GitHub
- Render

---

# 📱 Key Highlights
- Responsive UI design
- Modern dashboard interface
- Real-time score generation
- Dynamic leaderboard system
- Mobile-friendly layout

---

# 🎯 Use Cases
- 🎓 Student online quizzes
- 🧪 Practice tests
- 📚 Learning assessments
- 🏫 College mini projects

---

# 🔮 Future Enhancements
- 📧 Email notifications
- 🔐 OTP-based authentication
- 📱 Mobile application
- 📊 Advanced analytics dashboard
- 🧠 AI-based performance analysis

---

# 📂 Project Structure

```bash
Student_Quiz_Portal/
│── static/
│── templates/
│── app.py
│── database.sql
│── requirements.txt
│── README.md
