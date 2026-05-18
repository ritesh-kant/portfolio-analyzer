export const config = {
  mongodbUri: process.env.MONGODB_URI,
  allowedOrigin: process.env.ALLOWED_ORIGIN ?? 'http://localhost:3000',
  aiProvider: process.env.AI_PROVIDER ?? 'anthropic',
  signalEngineUrl: process.env.SIGNAL_ENGINE_URL ?? 'http://localhost:8000',
  signalEngineApiKey: process.env.SIGNAL_ENGINE_API_KEY ?? '',
  signalEngineFunctionName: process.env.SIGNAL_ENGINE_FUNCTION_NAME,
  sqsQueueUrl: process.env.SQS_QUEUE_URL,
  awsRegion: process.env.AWS_REGION ?? 'ap-south-1',
  queueProvider: process.env.QUEUE_PROVIDER ?? 'local',
};
