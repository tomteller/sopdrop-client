/**
 * Sopdrop Server
 * Houdini asset registry API
 */

import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';

// Routes
import assetsRouter from './routes/assets.js';
import versionsRouter from './routes/versions.js';
import authRouter from './routes/auth.js';
import usersRouter from './routes/users.js';
import reportsRouter from './routes/reports.js';
import favoritesRouter from './routes/favorites.js';
import commentsRouter from './routes/comments.js';
import collectionsRouter from './routes/collections.js';
import foldersRouter from './routes/folders.js';
import draftsRouter from './routes/drafts.js';
import feedbackRouter from './routes/feedback.js';
import moderationRouter from './routes/moderation.js';
import savedRouter from './routes/saved.js';
import teamsRouter from './routes/teams.js';
import teamLibraryRouter from './routes/teamLibrary.js';
import invitesRouter from './routes/invites.js';
import shareRouter, { cleanupExpiredShares } from './routes/share.js';
import { cleanupExpiredDrafts } from './routes/drafts.js';

// Middleware
import { errorHandler } from './middleware/errorHandler.js';
import { requestLogger } from './middleware/logger.js';
import {
  requestId,
  securityHeaders,
  httpsRedirect,
  generalLimiter,
  validateSecrets,
} from './middleware/security.js';

// Database
import { initDB, testConnection } from './models/db.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ============================================
// Security Validation (before anything else)
// ============================================
validateSecrets();

const app = express();
const PORT = process.env.PORT || 4848;
const HOST = process.env.HOST || '::';

// Trust proxy for correct IP detection behind load balancers
app.set('trust proxy', 1);

// Strong ETags on JSON responses so list endpoints can return 304 unchanged.
// Express's default is weak; strong gives us byte-identical revalidation.
app.set('etag', 'strong');

// ============================================
// Security Middleware (order matters!)
// ============================================

// 1. Request ID for tracing (first, so all logs have it)
app.use(requestId);

// 2. Security headers (helmet.js)
app.use(securityHeaders);

// 3. HTTPS redirect in production
app.use(httpsRedirect);

// 4. General rate limiting (fallback for all routes)
app.use(generalLimiter);

// ============================================
// Standard Middleware
// ============================================

// CORS - Configure allowed origins (explicit list always required)
const corsOrigins = process.env.CORS_ORIGINS?.split(',').map(o => o.trim()).filter(Boolean);
if (!corsOrigins || corsOrigins.length === 0) {
  if (process.env.NODE_ENV === 'production') {
    console.error('FATAL: CORS_ORIGINS environment variable is required in production');
    process.exit(1);
  }
  console.warn('⚠️  CORS_ORIGINS not set. Defaulting to localhost:5173 for development.');
}
app.use(cors({
  origin: corsOrigins && corsOrigins.length > 0 ? corsOrigins : ['http://localhost:5173'],
  credentials: true,
}));

// Request logging
app.use(requestLogger);

// Body parsing
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true, limit: '10mb' }));

// Static files
if (process.env.R2_BUCKET_NAME && process.env.R2_PUBLIC_URL) {
  // R2 mode: redirect /library/* requests to R2 public URL
  app.use('/library', (req, res) => {
    const key = req.path.replace(/^\//, '');
    const publicUrl = `${process.env.R2_PUBLIC_URL.replace(/\/$/, '')}/${key}`;
    res.redirect(301, publicUrl);
  });
} else {
  // Local mode: serve from filesystem.
  // Library files are content-addressed (UUID/hash filenames) and never
  // mutate after write, so we can serve them with immutable cache headers.
  // Big win for thumbnail-heavy panels: zero re-fetches on warm clients.
  const libraryPath = process.env.LIBRARY_PATH || path.join(process.cwd(), 'library');
  app.use('/library', express.static(libraryPath, {
    dotfiles: 'deny',
    index: false,
    immutable: true,
    maxAge: '365d',
  }));
}

// ============================================
// Health Check
// ============================================

app.get('/api/health', async (req, res) => {
  try {
    await testConnection();
    res.json({
      status: 'healthy',
      service: 'sopdrop',
      version: process.env.npm_package_version || '0.1.0',
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    res.status(503).json({
      status: 'unhealthy',
      error: error.message,
    });
  }
});

// ============================================
// API Routes
// ============================================

// v1 API
app.use('/api/v1/auth', authRouter);
app.use('/api/v1/users', usersRouter);
// Note: versionsRouter must come first because assetsRouter has /:slug(*) catch-all
app.use('/api/v1/assets', versionsRouter);
app.use('/api/v1/assets', assetsRouter);
app.use('/api/v1/reports', reportsRouter);  // Abuse reporting
app.use('/api/v1/favorites', favoritesRouter);
app.use('/api/v1/comments', commentsRouter);
app.use('/api/v1/collections', collectionsRouter);
app.use('/api/v1/folders', foldersRouter);
app.use('/api/v1/drafts', draftsRouter);
app.use('/api/v1/feedback', feedbackRouter);
app.use('/api/v1/moderation', moderationRouter);
app.use('/api/v1/saved', savedRouter);
// teamLibraryRouter must be mounted before teamsRouter so its /:slug/library/*
// routes take precedence (both routers share the /api/v1/teams namespace).
app.use('/api/v1/teams', teamLibraryRouter);
app.use('/api/v1/teams', teamsRouter);
app.use('/api/v1/invites', invitesRouter);
app.use('/api/v1/share', shareRouter);

// CLI auth endpoint (for token generation)
app.get('/auth/cli', (req, res) => {
  // Redirect to web UI for authentication
  const webUrl = process.env.WEB_URL || `http://localhost:5173`;
  res.redirect(`${webUrl}/auth/cli`);
});

// ============================================
// Error Handling
// ============================================

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: 'Not found' });
});

// Global error handler
app.use(errorHandler);

// ============================================
// Server Startup
// ============================================

async function startServer() {
  try {
    // Initialize database
    console.log('📦 Initializing database...');
    await initDB();

    // Start periodic cleanup jobs
    setInterval(cleanupExpiredShares, 60 * 60 * 1000); // Every hour
    setInterval(cleanupExpiredDrafts, 60 * 60 * 1000); // Every hour
    // Run once at startup (after short delay to let DB settle)
    setTimeout(() => {
      cleanupExpiredShares();
      cleanupExpiredDrafts();
    }, 10_000);

    // Start server
    app.listen(PORT, HOST, () => {
      console.log(`
╔═══════════════════════════════════════════════════╗
║                                                   ║
║   🍜 Sopdrop Server                               ║
║   Houdini Asset Registry                          ║
║                                                   ║
║   API: http://${HOST}:${PORT}/api/v1              ║
║                                                   ║
╚═══════════════════════════════════════════════════╝
      `);
    });
  } catch (error) {
    console.error('❌ Failed to start server:', error);
    process.exit(1);
  }
}

startServer();

export default app;
