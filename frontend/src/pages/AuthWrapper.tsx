import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';

interface AuthWrapperProps {
  children: React.ReactNode;
}

export default function AuthWrapper({ children }: AuthWrapperProps) {
  return (
    <Authenticator>
      {({ signOut, user }) => (
        <div className="min-h-screen bg-gradient-to-br from-background via-background to-muted/30">
          <nav className="border-b bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/60 shadow-modern sticky top-0 z-50">
            <div className="container flex h-16 items-center justify-between">
              <div className="flex items-center space-x-3">
                <div className="w-8 h-8 gradient-primary rounded-lg flex items-center justify-center">
                  <span className="text-white font-bold text-sm">PB</span>
                </div>
                <h2 className="text-xl font-bold bg-gradient-to-r from-primary to-accent bg-clip-text text-transparent">
                  ProofBrief
                </h2>
              </div>
              <div className="flex items-center space-x-4">
                <div className="hidden sm:flex items-center space-x-2">
                  <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse"></div>
                  <span className="text-sm text-muted-foreground">
                    {user?.username}
                  </span>
                </div>
                <button
                  onClick={signOut}
                  className="text-sm text-muted-foreground hover:text-foreground transition-colors duration-200 hover:bg-muted/50 px-3 py-1.5 rounded-md"
                >
                  Sign out
                </button>
              </div>
            </div>
          </nav>
          <main className="animate-fade-in">{children}</main>
        </div>
      )}
    </Authenticator>
  );
}