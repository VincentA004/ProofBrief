# ProofBrief Frontend

A React + TypeScript frontend for ProofBrief, built with Vite, AWS Amplify, and Tailwind CSS.

## Features

- 🔐 **Authentication**: AWS Cognito integration with Amplify UI
- 📄 **Brief Management**: Create, view, and manage candidate briefs
- 📤 **File Upload**: Resume PDF upload via presigned URLs
- 📊 **Real-time Status**: Auto-polling for brief processing status
- 📱 **Responsive Design**: Mobile-first, WCAG AA compliant
- ⚡ **Fast & Modern**: Built with Vite, TypeScript, and React Query

## Tech Stack

- **Frontend**: React 18 + TypeScript + Vite
- **Authentication**: AWS Amplify + Cognito
- **Styling**: Tailwind CSS + shadcn/ui components
- **State Management**: React Query
- **Routing**: React Router v6

## Setup

1. **Clone and install dependencies**:
   ```bash
   npm install
   ```

2. **Configure environment variables**:
   Copy `.env.example` to `.env` and fill in your AWS Cognito details:
   ```env
   VITE_REGION=us-east-1
   VITE_USER_POOL_ID=us-east-1_XXXXXXXXX
   VITE_USER_POOL_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
   VITE_API_BASE=https://<api-id>.execute-api.us-east-1.amazonaws.com/prod
   ```

3. **Run the development server**:
   ```bash
   npm run dev
   ```

4. **Build for production**:
   ```bash
   npm run build
   ```

## Application Flow

### Authentication
- Users sign in/up via AWS Cognito using Amplify Authenticator
- Authentication state is managed globally
- All API requests include Bearer token authentication

### Creating a Brief
1. Navigate to `/new`
2. Fill in candidate name and job title
3. Upload resume PDF file
4. Paste job description text
5. Submit form to create brief and upload files
6. Automatically navigate to brief detail page

### Brief Processing
1. Brief status starts as `PENDING`
2. Auto-polling every 5 seconds until status becomes `DONE`
3. When complete, download link becomes available

### Brief Management
- View all briefs at `/briefs`
- Click any brief to view details at `/briefs/:id`
- Real-time status updates and progress tracking

## API Integration

The frontend integrates with the ProofBrief backend API:

- `POST /briefs` - Create new brief with presigned upload URLs
- `PUT /briefs/:id/start` - Start brief processing pipeline
- `GET /briefs` - List user's briefs
- `GET /briefs/:id` - Get brief details and status

Files are uploaded directly to S3 via presigned URLs (resume as PDF, job description as text).

## Components Structure

```
src/
├── pages/
│   ├── AuthWrapper.tsx     # Authentication wrapper with nav
│   ├── BriefsList.tsx      # List all briefs
│   ├── NewBrief.tsx        # Create new brief form
│   └── BriefDetail.tsx     # Brief details and status
├── components/ui/          # shadcn/ui components
├── api.ts                  # API client functions
├── uploads.ts              # File upload helpers
├── types.ts                # TypeScript definitions
├── config.ts               # Environment configuration
└── amplify.ts              # Amplify configuration
```

## Error Handling

- Network errors display user-friendly toast messages
- Form validation prevents invalid submissions
- Upload progress tracking with step-by-step feedback
- Graceful handling of authentication failures

## Accessibility

- WCAG AA compliant design
- Semantic HTML structure
- Proper ARIA labels and focus management
- Keyboard navigation support
- Screen reader friendly

## Development

- TypeScript strict mode enabled
- ESLint + Prettier for code quality
- Responsive design testing
- Error boundary implementation
- Loading states and skeletons

## Lovable Project

**URL**: https://lovable.dev/projects/39eaf10a-5e09-4290-9d6f-14444d98672e

## Deployment

Build the application and deploy the `dist` folder to your preferred hosting service:

```bash
npm run build
```

The app is a static SPA that can be hosted on:
- AWS S3 + CloudFront  
- Vercel
- Netlify
- Any static hosting service

Simply open [Lovable](https://lovable.dev/projects/39eaf10a-5e09-4290-9d6f-14444d98672e) and click on Share -> Publish.
