// Environment configuration
export const config = {
  region: import.meta.env.VITE_REGION || 'us-east-1',
  userPoolId: import.meta.env.VITE_USER_POOL_ID || '',
  userPoolWebClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID || '',
  apiBase: import.meta.env.VITE_API_BASE?.replace(/\/$/, '') || '',
};