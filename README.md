# ProofBrief

[![AWS](https://img.shields.io/badge/Cloud-AWS-orange)](https://aws.amazon.com/)
[![React](https://img.shields.io/badge/Frontend-React-blue)](https://react.dev/)
[![Python](https://img.shields.io/badge/Backend-Python-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

**ProofBrief** is an AI-powered recruiting tool that turns a resume + job description into a **1-page, evidence-backed candidate brief** in ~90–120 seconds.  

Recruiters, hiring managers, and candidates benefit from **trust, speed, and actionability**:  
- ✅ **Trust** → Claims are verified against GitHub repos and time-stamped links  
- ⚡ **Speed** → Resumes are distilled into hiring-manager-ready briefs in minutes  
- 🎯 **Actionability** → Tailored screening questions + rubric-based scoring for decision-making  

This repository contains the **end-to-end system**, including frontend, backend, infrastructure-as-code, and AI-powered resume analysis pipeline.

## 👥 Authors

- [Vincent Allam](https://github.com/VincentA004) – [LinkedIn](https://www.linkedin.com/in/vincent-allam/)  
- [Antony Sajesh](https://github.com/ant-saj123) – [LinkedIn](https://www.linkedin.com/in/antony-sajesh/)  


## 🌐 Live Product

👉 [ProofBrief.com](https://www.proofbrief.com)

---

## 🚀 Product Summary

- **Primary User:** Recruiters & Sourcers  
- **Secondary Users:** Hiring Managers (brief consumption), Candidates (consent/redaction flow)  

**Core Value Proposition**  
- **Trust** → Evidence-backed claims verified with GitHub and time-stamped links  
- **Speed** → Hiring-manager-ready summaries generated in ~90–120 seconds  
- **Actionability** → Briefs include tailored screening questions + rubric-based scoring  

---

## ❓ Problem & Opportunity

Recruiters spend too much time:  
- Translating resumes into hiring-manager-ready summaries  
- Verifying candidate claims like *“improved X by Y%”*  
- Communicating consistently across hiring pipelines  

Most existing tools produce **generic summaries** without proof.  

**ProofBrief solves this by:**  
- Delivering **evidence-backed briefs** grounded in GitHub repos, READMEs, and code  
- Generating **concise, structured reports** ready for hiring managers  
- Providing **calibrated screening questions** to improve decision speed and quality

## 🧩 How It Works

ProofBrief converts a **resume + job description** into a verified candidate brief using a multi-step, AI-powered pipeline.

### 🔄 Pipeline Overview

1. **Upload**  
   - Resume (PDF) and Job Description (text) uploaded via frontend (React + Amplify).  
   - API Gateway + Cognito handle authentication and routing.  

2. **Parse Resume** (`parse_resume.py`)  
   - AWS Textract extracts raw text from resume PDF.  
   - GitHub profile links are pulled from both OCR text and embedded PDF hyperlinks.  

3. **Content Processing** (`process_content.py`)  
   - GitHub API scrapes candidate’s repositories.  
   - Claude-3-Haiku (AWS Bedrock) extracts technical skills into a **skill_map**.  
   - Top 3 repos are selected via LLM ranking, then READMEs + representative code files are bundled into S3.  
   - Heuristics (keyword density, recency, repo relevance) are computed.  

4. **Resume Agent** (`resume_agent.py`)  
   - Claude-3-Sonnet (AWS Bedrock) synthesizes a **final JSON brief** including:  
     - Candidate summary bullets  
     - Evidence highlights with clickable links  
     - Risk flags for unsupported claims  
     - Tailored screening questions  
     - Final score (0–100) with rubric-based adjudication  

5. **Save Output** (`save_output.py`)  
   - Final brief persisted to S3 and linked in Postgres RDS.  
   - Recruiter can retrieve result via frontend or API.

---

### ⚙️ Pipeline Orchestration

- All Lambda functions are coordinated with **AWS Step Functions**.  
- Each step (parse → process → agent → save) is a state in the state machine.  
- Logs and metrics flow into **CloudWatch** for observability.  

---

📊 **Result:** In under 2 minutes, recruiters receive a compact, evidence-backed candidate brief with linked proof and screening questions.

## 🛠 Technical Stack

ProofBrief is built as a **cloud-native, serverless application** with modular frontend, backend, and infrastructure layers.

---

### 🌐 Frontend
- **Framework:** React + Vite  
- **Hosting:** AWS Amplify (continuous deployment from GitHub)  
- **Features:**
  - Resume + JD upload flow
  - Authentication + session management via Cognito
  - Results dashboard to view briefs and download evidence
  - Responsive UI for recruiter and hiring manager usage

---

### ⚙️ Backend (Serverless Pipeline)

#### Core Lambdas
- **`api.py`** → API Gateway router (CRUD for briefs, auth with Cognito)  
- **`parse_resume.py`** → Textract OCR + GitHub link extraction  
- **`process_content.py`** → GitHub repo scraping, skill mapping, heuristics calculation  
- **`resume_agent.py`** → Claude-3-Sonnet synthesis of final JSON brief  
- **`save_output.py`** → Persist final brief in S3 + RDS  

#### Orchestration
- **AWS Step Functions** → Coordinates Lambdas in sequence  
- **CloudWatch Logs** → Monitoring & observability  
- **Secrets Manager** → GitHub API token + DB credentials  

---

### 🗄 Database Layer
- **Aurora Postgres (Serverless v2)** → Entity relationship store  
  - **Entities:** Users, Candidates, Jobs, Briefs, Artifacts  
  - Stores paths to S3 objects (resume, JD, processed repos, final brief)  
- **RDS Data API** used by Lambdas for lightweight, connectionless SQL  

---

### 📦 Storage
- **S3 Buckets**
  - Stores uploaded resumes & job descriptions  
  - Processed resume text + Textract JSON output  
  - Bundled GitHub READMEs + code samples  
  - Final candidate briefs (JSON reports)  
- Enforced with:
  - **SSL-only access**  
  - **Block Public Access**  
  - **Versioning + retention**  
  - **CORS rules** (for Amplify + localhost dev)  

---

### 🔑 Authentication & Security
- **Cognito User Pool**
  - Email-based signup/sign-in  
  - OAuth2 support for web clients  
  - Strong password enforcement + recovery flows  
- **IAM Roles**
  - Fine-grained policies per Lambda  
  - Access to RDS Data API, S3, Bedrock, Textract, and Secrets Manager  

---

### ☁️ Infrastructure-as-Code
- **AWS CDK (Python)** defines the entire stack:
  - VPC with public, private, and isolated subnets  
  - Security groups (Lambda → DB restricted ingress)  
  - Aurora DB cluster (serverless v2, min 0.5 ACU)  
  - S3 bucket with encryption + SSL enforcement  
  - Cognito User Pool + User Pool Client  
  - Step Function state machine (Parse → Process → ResumeAgent → Save)  
  - API Gateway REST API with Cognito authorizer  

---

🔒 **Security-first design:** Private subnets for database, IAM least privilege, and S3 encryption ensure candidate data is protected end-to-end.

## 🤖 AI / ML Layer with AWS Bedrock

ProofBrief uses **AWS Bedrock** to integrate Anthropic Claude models in a **two-tier architecture**:  

1. **Fast inference (Claude-3-Haiku)** → quick skill extraction, repo selection, heuristics  
2. **Deep inference (Claude-3-Sonnet)** → structured JSON brief with risk flags, screening questions, and final score  

This design balances **latency vs. depth**, ensuring recruiters receive results in ~90–120 seconds.

### 🟢 Stage 1: Fast Skill Extraction (Claude-3-Haiku)

- **Input:** Job description + resume text  
- **Tasks:**  
  - Extract technical skills into a `skill_map` (e.g., Python → {pandas, numpy})  
  - Cross-reference resume claims with GitHub repos  
  - Select top 3 repositories most relevant to candidate’s projects  
- **Why Haiku?**  
  - Low-latency inference for quick turnaround  
  - Small model → cost-efficient for bulk recruiter use  
  - JSON-enforced outputs (strict schema for skills & repo picks)

### 🔵 Stage 2: Deep Candidate Analysis (Claude-3-Sonnet)

- **Input:**  
  - Resume text (OCR + extracted URLs)  
  - Job description  
  - Selected GitHub READMEs + code snippets (bundled via S3)  
  - Skill heuristics (keyword density, recency, relevance)  

- **Tasks:**  
  - Generate **structured JSON brief** (summary, evidence, risks, questions, score)  
  - Apply **rubric-based scoring system** (0–100) with strict caps:  
    - Zero-Evidence → max 29  
    - Low-Depth → max 49  
    - Medium-Depth → max 69  
    - High-Depth → base score  

- **Why Sonnet?**  
  - Larger context window (handles resume + JD + repo code)  
  - More reliable JSON adherence for structured outputs  
  - Produces high-quality, HM-ready analysis

### 📐 Evidence Hierarchy (Strict Rules)

1. **Primary Source:** GitHub READMEs, manifests, and code  
2. **Secondary Source:** Resume project/work descriptions  
3. **Zero Weight:** Generic “skills” section without evidence  

⚠️ If a **required skill** is only found in the “skills” section and not evidenced in projects or code, the model applies a **-20 penalty** and caps the final score.



## 📊 Example Output

When a recruiter uploads a **resume + job description**, ProofBrief returns a **1-page JSON brief**.  
This brief can be rendered in the frontend for recruiters or consumed directly by hiring managers via API.

### Example JSON Brief

```json
{
  "summary": [
    "Strong Python + AWS depth evidenced by repos and resume projects",
    "Recent contributions to open-source ML pipelines",
    "Demonstrated cloud infra skills relevant to role"
  ],
  "evidence_highlights": [
    {
      "claim": "Built scalable data pipelines in AWS",
      "evidence_url": "https://github.com/user/repo",
      "justification": "Terraform + Lambda usage aligns with JD requirements"
    }
  ],
  "risk_flags": [
    "Kubernetes listed as skill but no verifiable project evidence"
  ],
  "screening_questions": [
    "How have you applied Terraform in production?",
    "Walk through an optimization you made in your ML repo.",
    "How do you ensure reliability in serverless pipelines?",
    "What are trade-offs between Aurora Serverless and provisioned Postgres?"
  ],
  "final_score": 74
  ```


## ☁️ Infrastructure & Deployment Flow

ProofBrief is deployed as a **serverless, cloud-native system** on AWS.  
The infrastructure is fully defined in **AWS CDK (Python)** for reproducibility and version control.

### 🏗 Core AWS Components

- **Frontend**
  - React + Vite hosted on **Amplify**
  - Authenticated via **Cognito User Pool**
  - Calls API Gateway endpoints directly

- **API Layer**
  - **API Gateway** → Entry point for frontend → backend  
  - **Cognito Authorizer** → Enforces secure access  

- **Backend Compute**
  - **AWS Lambda Functions**:
    - `api.py` → Routing + CRUD for briefs
    - `parse_resume.py` → Textract OCR + GitHub extraction
    - `process_content.py` → Repo scraping + skill mapping
    - `resume_agent.py` → Final Bedrock analysis
    - `save_output.py` → Store final results
  - **AWS Step Functions** orchestrates Lambdas in a sequential pipeline  

- **Data Layer**
  - **Aurora Serverless (Postgres v2)** → Entity relationships (users, candidates, jobs, briefs, artifacts)  
  - **Amazon S3** → Stores resumes, job descriptions, processed text, repo bundles, final briefs  

- **AI/ML Inference**
  - **AWS Bedrock**
    - Claude-3-Haiku for fast skill extraction
    - Claude-3-Sonnet for final synthesis
  - **Secrets Manager** → GitHub API token + DB credentials  


### 🔒 Security & Networking

- **VPC** with 3 subnet tiers:
  - Public → Amplify, API Gateway
  - Private → Lambdas
  - Isolated → Aurora DB
- **Security Groups**
  - Restrict Lambda → DB access on port 5432
- **S3 Bucket Security**
  - SSL-only access
  - Public access blocked
  - Versioning + encryption enforced
- **IAM Roles**
  - Fine-grained Lambda permissions (RDS Data API, Bedrock, Textract, S3, Secrets Manager)


### 📐 Deployment Flow (ASCII Diagram)

``` bash
[User/Recruiter]
       |
       v
 [Frontend: React + Amplify] 
       |
       v
 [API Gateway + Cognito Auth]
       |
       v
 ┌─────────────── AWS Step Functions ────────────────┐
 |   1. parse_resume.py (Textract OCR)               |
 |   2. process_content.py (GitHub + heuristics)     |
 |   3. resume_agent.py (Bedrock Claude-3 analysis)  |
 |   4. save_output.py (persist to S3 + RDS)         |
 └───────────────────────────────────────────────────┘
       |
       v
 [S3 + Aurora Postgres]
       |
       v
 [Recruiter retrieves final JSON brief → Hiring Manager]
 ```


## 🧑‍💻 Developer Guide

### 1. Clone & Bootstrap

```bash
git clone https://github.com/<ant-saj123>/proofbrief.git
cd proofbrief
make venv
```


---

### 2. Deploy Infrastructure

Deployment is orchestrated via **Makefile** targets. At minimum you must provide a GitHub token (for repo scraping) and an AWS region.

```bash
export AWS_REGION=us-east-1
export GITHUB_TOKEN=ghp_yourtokenhere
make deploy
```

This calls a series of Make targets under the hood:

- **`cdk-deploy`** → provisions all infra via CDK  
- **`gen-env`** → generates `backend/.env` with stack outputs  
- **`alembic-up`** → applies DB migrations  
- **`db-check`** → verifies Aurora Serverless is reachable  

### 3. End-to-End Test (pipeline sanity check)

Run the provided script to exercise the full flow (create brief → upload files → start → poll → fetch result):

```bash
chmod +x scripts/test_end_to_end.sh
./test_end_to_end.sh
```

What it does:

- Discovers your API Gateway URL from CloudFormation outputs  
- **POST /briefs** → returns presigned S3 PUT URLs  
- Uploads resume (PDF) + JD (text) via those URLs  
- **PUT /briefs/{id}/start** → kicks off Step Functions pipeline  
- Polls **GET /briefs/{id}** until `status="DONE"` and prints the final JSON brief  

### 4. Run Frontend Locally

Start the React frontend for development:

```bash
cd frontend
npm install
npm run dev
```

Create a `.env.local` file with your API and Cognito configuration:

```bash
VITE_API_BASE=https://<your-api-id>.execute-api.us-east-1.amazonaws.com/prod
VITE_COGNITO_CLIENT_ID=<your-cognito-client-id>
VITE_COGNITO_USER_POOL_ID=<your-cognito-pool-id>
```
The app will be available at **http://localhost:5173** by default.  

## 🔄 CI/CD & Future Enhancements

### ⚡ Continuous Integration
- **GitHub Actions** (planned) for:
  - Linting + formatting checks
  - Unit + integration test runs
  - CDK synth + diff checks
  - Backend Python tests with pytest
  - Frontend React tests with vitest/jest

### 🚀 Continuous Deployment
- **Frontend:** Auto-deployed from `main` branch to AWS Amplify  
- **Backend + Infra:** CDK pipelines or GitHub Actions workflows to run `make deploy`  
- **Secrets:** Managed via AWS Secrets Manager (GitHub token, DB creds)

### 🧭 Future Roadmap
- **Candidate Consent Flow** → allow candidates to redact repos before recruiter access  
- **Hiring Manager Portal** → view briefs directly with scoring filters  
- **Multi-model Evaluation** → experiment with Mistral, Llama, or Titan on Bedrock for alternative scoring  
- **Vector Search** → optional RAG layer across repos for deeper claim validation  
- **Analytics Dashboard** → recruiter productivity metrics (time saved, quality of hire improvements)  

---

ProofBrief is built as an **end-to-end cloud-native product**:  
- Recruiters save hours per candidate  
- Hiring managers get **trustworthy, evidence-backed briefs**  
- Candidates gain transparency into how they’re evaluated

## 🤝 Contributing

Contributions are welcome!  
If you’d like to help improve ProofBrief, please:

1. Fork the repo  
2. Create a feature branch (`git checkout -b feature/my-feature`)  
3. Commit your changes (`git commit -m "Add feature"`)  
4. Push to your branch (`git push origin feature/my-feature`)  
5. Open a Pull Request  

Please ensure all new code passes linting, tests, and deployment checks before submitting a PR.

---

## 📜 License

This project is licensed under the **MIT License**.  
See the [LICENSE](LICENSE) file for details.
