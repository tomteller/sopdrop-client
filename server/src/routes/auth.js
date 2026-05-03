/**
 * Authentication routes
 */

import { Router } from 'express';
import crypto from 'crypto';
// bcrypt removed — no more password auth
import { OAuth2Client } from 'google-auth-library';
import { query, getClient } from '../models/db.js';
import { authenticate, signJWT, generateToken, hashToken, promoteIfFirstUser } from '../middleware/auth.js';
import { ValidationError, AuthError, ConflictError } from '../middleware/errorHandler.js';
import {
  registerLimiter,
  oauthLimiter,
  tokenLimiter,
  passwordChangeLimiter,
  logAuthEvent,
  isReservedUsername,
} from '../middleware/security.js';

const googleClient = process.env.GOOGLE_CLIENT_ID
  ? new OAuth2Client(process.env.GOOGLE_CLIENT_ID)
  : null;

const router = Router();

/**
 * Check if beta mode is enabled
 */
const BETA_MODE = process.env.BETA_MODE !== 'false'; // Default to true

/**
 * Validate and consume an invite code
 */
async function validateAndUseInviteCode(code, userId) {
  if (!code) {
    throw new ValidationError('Invite code is required during closed beta');
  }

  const result = await query(`
    SELECT id, max_uses, use_count, expires_at, created_by
    FROM invite_codes
    WHERE code = $1
  `, [code.toUpperCase()]);

  if (result.rows.length === 0) {
    throw new ValidationError('Invalid invite code');
  }

  const invite = result.rows[0];

  // Check if expired
  if (invite.expires_at && new Date(invite.expires_at) < new Date()) {
    throw new ValidationError('This invite code has expired');
  }

  // Check if max uses reached
  if (invite.use_count >= invite.max_uses) {
    throw new ValidationError('This invite code has already been used');
  }

  // Increment use count and record usage
  await query(`
    UPDATE invite_codes
    SET use_count = use_count + 1,
        used_by = COALESCE(used_by, $1),
        used_at = COALESCE(used_at, NOW())
    WHERE id = $2
  `, [userId, invite.id]);

  return invite.created_by;
}

/**
 * POST /auth/register
 * Email/password registration removed — use Discord or Google OAuth
 */
router.post('/register', (req, res) => {
  res.status(410).json({
    error: 'Email/password registration has been removed. Please use Discord or Google OAuth.',
    code: 'AUTH_METHOD_REMOVED',
  });
});

/**
 * POST /auth/login
 * Email/password login removed — use Discord or Google OAuth
 */
router.post('/login', (req, res) => {
  res.status(410).json({
    error: 'Email/password login has been removed. Please use Discord or Google OAuth.',
    code: 'AUTH_METHOD_REMOVED',
  });
});

/**
 * GET /auth/me
 * Get current user profile
 */
router.get('/me', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      SELECT
        id, username, email, display_name, avatar_url, bio, website,
        company, job_title, location, social_links,
        is_verified, is_admin, role, asset_count, download_count, created_at
      FROM users
      WHERE id = $1
    `, [req.user.id]);

    if (result.rows.length === 0) {
      throw new AuthError('User not found');
    }

    const user = result.rows[0];

    res.json({
      id: user.id,
      username: user.username,
      email: user.email,
      displayName: user.display_name,
      avatarUrl: user.avatar_url,
      bio: user.bio,
      website: user.website,
      company: user.company,
      jobTitle: user.job_title,
      location: user.location,
      socialLinks: user.social_links || {},
      isVerified: user.is_verified,
      isAdmin: user.is_admin,
      role: user.role || 'user',
      assetCount: user.asset_count,
      downloadCount: user.download_count,
      createdAt: user.created_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /auth/tokens
 * Generate a new API token
 */
router.post('/tokens', authenticate, tokenLimiter, async (req, res, next) => {
  try {
    const { name, scopes, expiresIn } = req.body;

    if (!name) {
      throw new ValidationError('Token name is required');
    }

    if (name.length > 100) {
      throw new ValidationError('Token name must be 100 characters or less');
    }

    // Generate token
    const token = generateToken();
    const tokenHash = hashToken(token);

    // Calculate expiration
    let expiresAt = null;
    if (expiresIn) {
      const days = parseInt(expiresIn);
      if (days > 0 && days <= 365) {
        expiresAt = new Date(Date.now() + days * 24 * 60 * 60 * 1000);
      }
    }

    // Insert token
    const result = await query(`
      INSERT INTO api_tokens (user_id, token_hash, name, scopes, expires_at)
      VALUES ($1, $2, $3, $4, $5)
      RETURNING id, name, scopes, expires_at, created_at
    `, [
      req.user.id,
      tokenHash,
      name,
      scopes || ['read', 'write'],
      expiresAt,
    ]);

    const tokenRecord = result.rows[0];

    // Log token creation
    logAuthEvent('token_created', req, {
      targetType: 'api_token',
      targetId: tokenRecord.id.toString(),
      tokenName: name,
      scopes: tokenRecord.scopes,
    });

    // Return the token (only time it's shown in plain text)
    res.status(201).json({
      token,  // Only returned once!
      id: tokenRecord.id,
      name: tokenRecord.name,
      scopes: tokenRecord.scopes,
      expiresAt: tokenRecord.expires_at,
      createdAt: tokenRecord.created_at,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /auth/tokens
 * List user's API tokens (without the actual token values)
 */
router.get('/tokens', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      SELECT id, name, scopes, last_used_at, expires_at, created_at
      FROM api_tokens
      WHERE user_id = $1
      ORDER BY created_at DESC
    `, [req.user.id]);

    res.json({
      tokens: result.rows.map(t => ({
        id: t.id,
        name: t.name,
        scopes: t.scopes,
        lastUsedAt: t.last_used_at,
        expiresAt: t.expires_at,
        createdAt: t.created_at,
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /auth/tokens/:id
 * Revoke an API token
 */
router.delete('/tokens/:id', authenticate, async (req, res, next) => {
  try {
    const result = await query(`
      DELETE FROM api_tokens
      WHERE id = $1 AND user_id = $2
      RETURNING id, name
    `, [req.params.id, req.user.id]);

    if (result.rows.length === 0) {
      throw new ValidationError('Token not found');
    }

    // Log token revocation
    logAuthEvent('token_revoked', req, {
      targetType: 'api_token',
      targetId: req.params.id,
      tokenName: result.rows[0].name,
    });

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /auth/verify-email — removed (OAuth auto-verifies)
 */
router.post('/verify-email', (req, res) => {
  res.status(410).json({
    error: 'Email verification is handled automatically through OAuth.',
    code: 'AUTH_METHOD_REMOVED',
  });
});

/**
 * PUT /auth/password — removed (no password auth)
 */
router.put('/password', (req, res) => {
  res.status(410).json({
    error: 'Password management has been removed. Accounts now use OAuth only.',
    code: 'AUTH_METHOD_REMOVED',
  });
});

/**
 * DELETE /auth/delete-account
 * Permanently delete user account (confirm by typing username)
 */
router.delete('/delete-account', authenticate, async (req, res, next) => {
  const client = await getClient();

  try {
    const { confirmation } = req.body;

    // Get user info
    const userResult = await query(`
      SELECT id, username FROM users WHERE id = $1
    `, [req.user.id]);

    const user = userResult.rows[0];

    if (!confirmation || confirmation !== user.username) {
      throw new ValidationError('Please type your username to confirm account deletion');
    }

    // Begin transaction
    await client.query('BEGIN');

    // Delete user's API tokens
    await client.query('DELETE FROM api_tokens WHERE user_id = $1', [user.id]);

    // Delete user's favorites
    await client.query('DELETE FROM favorites WHERE user_id = $1', [user.id]);

    // Delete user's comments
    await client.query('DELETE FROM comments WHERE user_id = $1', [user.id]);

    // Delete user's saved assets
    await client.query('DELETE FROM saved_assets WHERE user_id = $1', [user.id]);

    // For assets, we could either:
    // 1. Delete them entirely
    // 2. Keep them but mark as orphaned
    // 3. Transfer to a system account
    // For now, we'll mark assets as from a deleted user
    await client.query(`
      UPDATE assets SET owner_id = NULL, updated_at = NOW()
      WHERE owner_id = $1
    `, [user.id]);

    // Delete the user
    await client.query('DELETE FROM users WHERE id = $1', [user.id]);

    await client.query('COMMIT');

    logAuthEvent('account_deleted', req, {
      targetType: 'user',
      targetId: user.username,
      userId: user.id,
    });

    res.json({
      success: true,
      message: 'Account deleted successfully',
    });
  } catch (error) {
    await client.query('ROLLBACK');
    next(error);
  } finally {
    client.release();
  }
});

/**
 * POST /auth/resend-verification — removed (OAuth auto-verifies)
 */
router.post('/resend-verification', (req, res) => {
  res.status(410).json({
    error: 'Email verification is handled automatically through OAuth.',
    code: 'AUTH_METHOD_REMOVED',
  });
});

/**
 * GET /auth/beta-status
 * Check if beta mode is enabled and if an invite code is required
 */
router.get('/beta-status', (req, res) => {
  res.json({
    betaMode: BETA_MODE,
    inviteRequired: BETA_MODE,
  });
});

/**
 * POST /auth/oauth/google
 * Handle Google OAuth callback
 */
router.post('/oauth/google', oauthLimiter, async (req, res, next) => {
  try {
    const { credential, inviteCode } = req.body;

    if (!credential) {
      throw new ValidationError('Google credential is required');
    }

    // Verify the Google ID token signature
    if (!googleClient) {
      throw new ValidationError('Google OAuth is not configured');
    }

    let payload;
    try {
      const ticket = await googleClient.verifyIdToken({
        idToken: credential,
        audience: process.env.GOOGLE_CLIENT_ID,
      });
      payload = ticket.getPayload();
    } catch (err) {
      throw new ValidationError('Invalid Google credential');
    }

    if (!payload.email || !payload.sub) {
      throw new ValidationError('Invalid Google credential payload');
    }

    const { email, sub: googleId, name, picture } = payload;

    // Check if user exists by Google ID
    let userResult = await query(`
      SELECT id, username, email, display_name, avatar_url, is_verified, is_admin, role, email_verified
      FROM users
      WHERE google_id = $1
    `, [googleId]);

    let user = userResult.rows[0];
    let isNewUser = false;

    // OAuth login proves email ownership — ensure email_verified is true
    if (user && !user.email_verified) {
      await query('UPDATE users SET email_verified = true WHERE id = $1', [user.id]);
      user.email_verified = true;
    }

    if (!user) {
      // Check if email is already registered
      userResult = await query('SELECT id, google_id FROM users WHERE email = $1', [email.toLowerCase()]);

      if (userResult.rows.length > 0) {
        // Link Google to existing account
        const existingUser = userResult.rows[0];
        if (existingUser.google_id) {
          throw new ValidationError('This email is already linked to a different Google account');
        }

        await query('UPDATE users SET google_id = $1, email_verified = true WHERE id = $2', [googleId, existingUser.id]);

        userResult = await query(`
          SELECT id, username, email, display_name, avatar_url, is_verified, is_admin, role, email_verified
          FROM users WHERE id = $1
        `, [existingUser.id]);
        user = userResult.rows[0];
      } else {
        // Create new user - need invite code in beta
        if (BETA_MODE && !inviteCode) {
          throw new ValidationError('An invite code is required to register during closed beta');
        }

        // Generate username from email
        let baseUsername = email.split('@')[0].toLowerCase().replace(/[^a-z0-9_-]/g, '');
        if (baseUsername.length < 3) baseUsername = 'user' + baseUsername;
        let username = baseUsername;
        let suffix = 1;

        while (true) {
          const existing = await query('SELECT id FROM users WHERE username = $1', [username]);
          if (existing.rows.length === 0) break;
          username = `${baseUsername}${suffix}`;
          suffix++;
        }

        const insertResult = await query(`
          INSERT INTO users (username, email, google_id, display_name, avatar_url, email_verified, invite_code_used)
          VALUES ($1, $2, $3, $4, $5, true, $6)
          RETURNING id, username, email, display_name, avatar_url, is_verified, is_admin, role, email_verified
        `, [username, email.toLowerCase(), googleId, name || username, picture || null, inviteCode?.toUpperCase() || null]);

        user = insertResult.rows[0];
        isNewUser = true;

        // First user on a fresh server becomes the owner/admin (no-op
        // once any admin exists). Avoids the chicken-and-egg of needing
        // to manually promote yourself before you can do anything
        // gated on admin (e.g. preserve-authorship migration).
        if (await promoteIfFirstUser(user.id)) {
          user.is_admin = true;
          user.role = 'owner';
        }

        // Validate and consume invite code
        if (BETA_MODE && inviteCode) {
          try {
            const invitedBy = await validateAndUseInviteCode(inviteCode, user.id);
            if (invitedBy) {
              await query('UPDATE users SET invited_by = $1 WHERE id = $2', [invitedBy, user.id]);
            }
          } catch (inviteError) {
            await query('DELETE FROM users WHERE id = $1', [user.id]);
            throw inviteError;
          }
        }

        logAuthEvent('register_oauth', req, {
          targetType: 'user',
          targetId: user.username,
          userId: user.id,
          provider: 'google',
        });
      }
    }

    logAuthEvent('login_oauth', req, {
      targetType: 'user',
      targetId: user.username,
      userId: user.id,
      provider: 'google',
    });

    const token = signJWT({
      sub: user.id,
      username: user.username,
    });

    res.json({
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        displayName: user.display_name,
        avatarUrl: user.avatar_url,
        isVerified: user.is_verified,
        isAdmin: user.is_admin,
        role: user.role || 'user',
        emailVerified: user.email_verified,
      },
      token,
      isNewUser,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /auth/oauth/discord
 * Handle Discord OAuth callback
 */
router.post('/oauth/discord', oauthLimiter, async (req, res, next) => {
  try {
    const { code, inviteCode } = req.body;

    if (!code) {
      throw new ValidationError('Discord authorization code is required');
    }

    // Exchange code for access token
    const tokenResponse = await fetch('https://discord.com/api/oauth2/token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        client_id: process.env.DISCORD_CLIENT_ID,
        client_secret: process.env.DISCORD_CLIENT_SECRET,
        grant_type: 'authorization_code',
        code,
        redirect_uri: process.env.DISCORD_REDIRECT_URI || `${process.env.WEB_URL}/login/discord/callback`,
      }),
    });

    if (!tokenResponse.ok) {
      const errorData = await tokenResponse.json().catch(() => ({}));
      console.error('Discord token exchange failed:', errorData);
      throw new ValidationError('Failed to authenticate with Discord');
    }

    const tokenData = await tokenResponse.json();

    // Get user info from Discord
    const userResponse = await fetch('https://discord.com/api/users/@me', {
      headers: {
        Authorization: `Bearer ${tokenData.access_token}`,
      },
    });

    if (!userResponse.ok) {
      throw new ValidationError('Failed to get Discord user info');
    }

    const discordUser = await userResponse.json();
    const { id: discordId, email, username: discordUsername, global_name, avatar } = discordUser;

    if (!email) {
      throw new ValidationError('Discord account must have a verified email');
    }

    // Check if user exists by Discord ID
    let userResult = await query(`
      SELECT id, username, email, display_name, avatar_url, is_verified, is_admin, role, email_verified
      FROM users
      WHERE discord_id = $1
    `, [discordId]);

    let user = userResult.rows[0];
    let isNewUser = false;

    // OAuth login proves email ownership — ensure email_verified is true
    if (user && !user.email_verified) {
      await query('UPDATE users SET email_verified = true WHERE id = $1', [user.id]);
      user.email_verified = true;
    }

    if (!user) {
      // Check if email is already registered
      userResult = await query('SELECT id, discord_id FROM users WHERE email = $1', [email.toLowerCase()]);

      if (userResult.rows.length > 0) {
        // Link Discord to existing account
        const existingUser = userResult.rows[0];
        if (existingUser.discord_id) {
          throw new ValidationError('This email is already linked to a different Discord account');
        }

        await query('UPDATE users SET discord_id = $1, email_verified = true WHERE id = $2', [discordId, existingUser.id]);

        userResult = await query(`
          SELECT id, username, email, display_name, avatar_url, is_verified, is_admin, role, email_verified
          FROM users WHERE id = $1
        `, [existingUser.id]);
        user = userResult.rows[0];
      } else {
        // Create new user - need invite code in beta
        if (BETA_MODE && !inviteCode) {
          throw new ValidationError('An invite code is required to register during closed beta');
        }

        // Generate username from Discord username
        let baseUsername = discordUsername.toLowerCase().replace(/[^a-z0-9_-]/g, '');
        if (baseUsername.length < 3) baseUsername = 'user' + baseUsername;
        let username = baseUsername;
        let suffix = 1;

        while (true) {
          const existing = await query('SELECT id FROM users WHERE username = $1', [username]);
          if (existing.rows.length === 0) break;
          username = `${baseUsername}${suffix}`;
          suffix++;
        }

        // Discord avatar URL
        const avatarUrl = avatar
          ? `https://cdn.discordapp.com/avatars/${discordId}/${avatar}.png`
          : null;

        const insertResult = await query(`
          INSERT INTO users (username, email, discord_id, display_name, avatar_url, email_verified, invite_code_used)
          VALUES ($1, $2, $3, $4, $5, true, $6)
          RETURNING id, username, email, display_name, avatar_url, is_verified, is_admin, role, email_verified
        `, [username, email.toLowerCase(), discordId, global_name || discordUsername, avatarUrl, inviteCode?.toUpperCase() || null]);

        user = insertResult.rows[0];
        isNewUser = true;

        // First user on a fresh server becomes the owner/admin (no-op
        // once any admin exists). Mirrors the Google branch above.
        if (await promoteIfFirstUser(user.id)) {
          user.is_admin = true;
          user.role = 'owner';
        }

        // Validate and consume invite code
        if (BETA_MODE && inviteCode) {
          try {
            const invitedBy = await validateAndUseInviteCode(inviteCode, user.id);
            if (invitedBy) {
              await query('UPDATE users SET invited_by = $1 WHERE id = $2', [invitedBy, user.id]);
            }
          } catch (inviteError) {
            await query('DELETE FROM users WHERE id = $1', [user.id]);
            throw inviteError;
          }
        }

        logAuthEvent('register_oauth', req, {
          targetType: 'user',
          targetId: user.username,
          userId: user.id,
          provider: 'discord',
        });
      }
    }

    logAuthEvent('login_oauth', req, {
      targetType: 'user',
      targetId: user.username,
      userId: user.id,
      provider: 'discord',
    });

    const token = signJWT({
      sub: user.id,
      username: user.username,
    });

    res.json({
      user: {
        id: user.id,
        username: user.username,
        email: user.email,
        displayName: user.display_name,
        avatarUrl: user.avatar_url,
        isVerified: user.is_verified,
        isAdmin: user.is_admin,
        role: user.role || 'user',
        emailVerified: user.email_verified,
      },
      token,
      isNewUser,
    });
  } catch (error) {
    next(error);
  }
});

export default router;
