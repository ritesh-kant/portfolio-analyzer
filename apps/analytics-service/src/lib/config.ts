export const config = {
  mongodbUri: process.env.MONGODB_URI,
  allowedOrigin: process.env.ALLOWED_ORIGIN ?? 'http://localhost:3000',
};
