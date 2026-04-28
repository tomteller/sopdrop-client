/**
 * Invite code routes for closed beta
 */

import { Router } from 'express';
import crypto from 'crypto';
import { query } from '../models/db.js';
import { authenticate } from '../middleware/auth.js';
import { ValidationError, NotFoundError, AuthError } from '../middleware/errorHandler.js';

const router = Router();

/**
 * Generate a random invite code
 */
function generateInviteCode() {
  // Format: XXXX-XXXX (8 chars, easy to type)
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // Removed confusing chars (0,O,1,I)
  let code = '';
  for (let i = 0; i < 8; i++) {
    if (i === 4) code += '-';
    code += chars[crypto.randomInt(chars.length)];
  }
  return code;
}

/**
 * POST /invites
 * Create a new invite code (admin/mod only, or users with invite privileges)
 */
router.post('/', authenticate, async (req, res, next) => {
  try {
    const { note, maxUses = 1, expiresInDays } = req.body;

    // Check if user can create invites
    const canInvite = ['owner', 'admin', 'moderator'].includes(req.user.role);
    if (!canInvite) {
      throw new AuthError('You do not have permission to create invite codes');
    }

    // Generate unique code
    let code;
    let attempts = 0;
    while (attempts < 10) {
      code = generateInviteCode();
      const existing = await query('SELECT id FROM invite_codes WHERE code = $1', [code]);
      if (existing.rows.length === 0) break;
      attempts++;
    }

    if (attempts >= 10) {
      throw new Error('Failed to generate unique invite code');
    }

    // Calculate expiration
    let expiresAt = null;
    if (expiresInDays && expiresInDays > 0) {
      expiresAt = new Date(Date.now() + expiresInDays * 24 * 60 * 60 * 1000);
    }

    const result = await query(`
      INSERT INTO invite_codes (code, created_by, max_uses, note, expires_at)
      VALUES ($1, $2, $3, $4, $5)
      RETURNING id, code, max_uses, use_count, note, expires_at, created_at
    `, [code, req.user.id, maxUses, note || null, expiresAt]);

    const invite = result.rows[0];

    res.status(201).json({
      id: invite.id,
      code: invite.code,
      maxUses: invite.max_uses,
      useCount: invite.use_count,
      note: invite.note,
      expiresAt: invite.expires_at,
      createdAt: invite.created_at,
      // Full invite URL for easy sharing
      inviteUrl: `${process.env.WEB_URL || 'https://sopdrop.com'}/register?invite=${invite.code}`,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /invites
 * List invite codes created by the current user (or all for admins)
 */
router.get('/', authenticate, async (req, res, next) => {
  try {
    const canViewAll = ['owner', 'admin'].includes(req.user.role);

    let result;
    if (canViewAll) {
      result = await query(`
        SELECT
          ic.id, ic.code, ic.max_uses, ic.use_count, ic.note,
          ic.expires_at, ic.created_at, ic.used_at,
          creator.username as created_by_username,
          used_user.username as used_by_username
        FROM invite_codes ic
        LEFT JOIN users creator ON ic.created_by = creator.id
        LEFT JOIN users used_user ON ic.used_by = used_user.id
        ORDER BY ic.created_at DESC
        LIMIT 100
      `);
    } else {
      result = await query(`
        SELECT
          ic.id, ic.code, ic.max_uses, ic.use_count, ic.note,
          ic.expires_at, ic.created_at, ic.used_at,
          used_user.username as used_by_username
        FROM invite_codes ic
        LEFT JOIN users used_user ON ic.used_by = used_user.id
        WHERE ic.created_by = $1
        ORDER BY ic.created_at DESC
        LIMIT 100
      `, [req.user.id]);
    }

    res.json({
      invites: result.rows.map(i => ({
        id: i.id,
        code: i.code,
        maxUses: i.max_uses,
        useCount: i.use_count,
        note: i.note,
        expiresAt: i.expires_at,
        createdAt: i.created_at,
        usedAt: i.used_at,
        createdByUsername: i.created_by_username,
        usedByUsername: i.used_by_username,
        isValid: i.use_count < i.max_uses && (!i.expires_at || new Date(i.expires_at) > new Date()),
      })),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /invites/validate/:code
 * Check if an invite code is valid (public endpoint)
 */
router.get('/validate/:code', async (req, res, next) => {
  try {
    const { code } = req.params;

    const result = await query(`
      SELECT id, code, max_uses, use_count, expires_at
      FROM invite_codes
      WHERE code = $1
    `, [code.toUpperCase()]);

    if (result.rows.length === 0) {
      return res.json({ valid: false, reason: 'Invalid invite code' });
    }

    const invite = result.rows[0];

    // Check if expired
    if (invite.expires_at && new Date(invite.expires_at) < new Date()) {
      return res.json({ valid: false, reason: 'This invite code has expired' });
    }

    // Check if max uses reached
    if (invite.use_count >= invite.max_uses) {
      return res.json({ valid: false, reason: 'This invite code has already been used' });
    }

    res.json({ valid: true });
  } catch (error) {
    next(error);
  }
});

/**
 * DELETE /invites/:id
 * Revoke an invite code
 */
router.delete('/:id', authenticate, async (req, res, next) => {
  try {
    const canDelete = ['owner', 'admin'].includes(req.user.role);

    let result;
    if (canDelete) {
      result = await query('DELETE FROM invite_codes WHERE id = $1 RETURNING id', [req.params.id]);
    } else {
      result = await query('DELETE FROM invite_codes WHERE id = $1 AND created_by = $2 RETURNING id', [req.params.id, req.user.id]);
    }

    if (result.rows.length === 0) {
      throw new NotFoundError('Invite code not found');
    }

    res.json({ success: true });
  } catch (error) {
    next(error);
  }
});

export default router;
