export const config = {
  mongodbUri: process.env.MONGODB_URI,
  allowedOrigin: process.env.ALLOWED_ORIGIN ?? 'http://localhost:3000',
  zerodhaApiKey: process.env.ZERODHA_API_KEY,
  zerodhaAccessToken: process.env.ZERODHA_ACCESS_TOKEN,
};
