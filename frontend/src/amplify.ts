import { Amplify } from 'aws-amplify';
import { config } from './config';

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: config.userPoolId,
      userPoolClientId: config.userPoolWebClientId,
    }
  }
});