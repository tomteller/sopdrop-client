/**
 * Moderation routes
 * For managing user-submitted content and users
 */

import { Router } from 'express';
import { query } from '../models/db.js';
import { authenticate, requireMod, requireRole, hasRole } from '../middleware/auth.js';
import { ValidationError, NotFoundError, ForbiddenError } from '../middleware/errorHandler.js';
import { logAuditEvent, sanitizePlainText } from '../middleware/security.js';

const router = Router();

// All moderation routes require authentication + moderator role
router.use(authenticate);
router.use(requireMod);

// Valid action types for logging
const ACTION_TYPES = {
  asset: ['hide_asset', 'remove_asset', 'restore_asset'],
  user: ['warn_user', 'suspend_user', 'ban_user', 'unban_user', 'change_role'],
  report: ['dismiss_report'],
};

/**
 * Log a moderation action
 */
async function logModAction(moderatorId, actionType, targetType, targetId, reason, notes = null, metadata = {}) {
  await query(`
    INSERT INTO mod_actions (moderator_id, action_type, target_type, target_id, reason, notes, metadata)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
  `, [moderatorId, actionType, targetType, targetId, reason, notes, JSON.stringify(metadata)]);
}

// ============================================
// MODERATION QUEUE
// ============================================

/**
 * GET /moderation/queue
 * Get the moderation queue (pending reports + assets under review)
 */
router.get('/queue', async (req, res, next) => {
  try {
    // Get pending abuse reports
    const reportsResult = await query(`
      SELECT
        r.*,
        reporter.username as reporter_username,
        CASE
          WHEN r.target_type = 'asset' THEN (
            SELECT json_build_object('name', a.name, 'owner', u.username)
            FROM assets a
            JOIN users u ON a.owner_id = u.id
            WHERE u.username || '/' || a.slug = r.target_id
          )
          WHEN r.target_type = 'user' THEN (
            SELECT json_build_object('username', u.username, 'display_name', u.display_name)
            FROM users u
            WHERE u.username = r.target_id
          )
        END as target_info
      FROM abuse_reports r
      LEFT JOIN users reporter ON r.reporter_id = reporter.id
      WHERE r.status = 'pending'
      ORDER BY r.created_at ASC
      LIMIT 100
    `);

    // Get assets under review
    const assetsResult = await query(`
      SELECT
        a.id, a.asset_id, a.name, a.slug, a.status,
        a.removed_reason, a.removed_at,
        u.username as owner_username,
        remover.username as removed_by_username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN users remover ON a.removed_by = remover.id
      WHERE a.status IN ('under_review', 'hidden')
      ORDER BY a.updated_at DESC
      LIMIT 50
    `);

    // Get users with warnings/suspensions
    const usersResult = await query(`
      SELECT
        id, username, display_name, email, status,
        strike_count, suspended_until, ban_reason, role,
        created_at
      FROM users
      WHERE status IN ('warned', 'suspended')
      ORDER BY
        CASE status
          WHEN 'suspended' THEN 1
          WHEN 'warned' THEN 2
        END,
        updated_at DESC
      LIMIT 50
    `);

    // Get counts
    const countsResult = await query(`
      SELECT
        (SELECT COUNT(*) FROM abuse_reports WHERE status = 'pending') as pending_reports,
        (SELECT COUNT(*) FROM assets WHERE status = 'under_review') as assets_under_review,
        (SELECT COUNT(*) FROM assets WHERE status = 'hidden') as hidden_assets,
        (SELECT COUNT(*) FROM users WHERE status = 'warned') as warned_users,
        (SELECT COUNT(*) FROM users WHERE status = 'suspended') as suspended_users,
        (SELECT COUNT(*) FROM users WHERE status = 'banned') as banned_users
    `);

    res.json({
      reports: reportsResult.rows,
      assets: assetsResult.rows,
      users: usersResult.rows,
      counts: countsResult.rows[0],
    });
  } catch (error) {
    next(error);
  }
});

// ============================================
// ASSET MODERATION
// ============================================

/**
 * GET /moderation/assets
 * List assets with optional status filter
 */
router.get('/assets', async (req, res, next) => {
  try {
    const { status, limit = 50, offset = 0, search } = req.query;
    const validStatuses = ['published', 'hidden', 'under_review', 'removed'];

    let whereClause = '1=1';
    const params = [];
    let paramIndex = 1;

    if (status && validStatuses.includes(status)) {
      whereClause += ` AND a.status = $${paramIndex++}`;
      params.push(status);
    }

    if (search) {
      whereClause += ` AND (a.name ILIKE $${paramIndex} OR a.slug ILIKE $${paramIndex} OR u.username ILIKE $${paramIndex})`;
      params.push(`%${search}%`);
      paramIndex++;
    }

    params.push(Math.min(parseInt(limit) || 50, 100));
    params.push(parseInt(offset) || 0);

    const result = await query(`
      SELECT
        a.id, a.asset_id, a.name, a.slug, a.status, a.asset_type,
        a.description, a.download_count, a.is_public,
        a.removed_reason, a.removed_at,
        a.created_at, a.updated_at,
        u.username as owner_username,
        remover.username as removed_by_username
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      LEFT JOIN users remover ON a.removed_by = remover.id
      WHERE ${whereClause}
      ORDER BY a.updated_at DESC
      LIMIT $${paramIndex++} OFFSET $${paramIndex}
    `, params);

    const countResult = await query(`
      SELECT COUNT(*) as total
      FROM assets a
      JOIN users u ON a.owner_id = u.id
      WHERE ${whereClause}
    `, params.slice(0, -2));

    res.json({
      assets: result.rows,
      total: parseInt(countResult.rows[0].total),
      limit: parseInt(limit),
      offset: parseInt(offset),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/assets/:id/hide
 * Temporarily hide an asset (pending review)
 */
router.post('/assets/:id/hide', async (req, res, next) => {
  try {
    const { reason } = req.body;

    if (!reason || reason.trim().length < 10) {
      throw new ValidationError('Reason is required (minimum 10 characters)');
    }

    const result = await query(`
      UPDATE assets
      SET status = 'hidden',
          removed_reason = $1,
          removed_by = $2,
          removed_at = NOW(),
          updated_at = NOW()
      WHERE id = $3
      RETURNING id, name, slug, status
    `, [sanitizePlainText(reason, 1000), req.user.id, req.params.id]);

    if (result.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const asset = result.rows[0];

    await logModAction(req.user.id, 'hide_asset', 'asset', asset.id, reason);
    logAuditEvent('asset_hidden', 'MODERATION', req, {
      assetId: asset.id,
      assetSlug: asset.slug,
    });

    res.json({
      success: true,
      message: 'Asset hidden',
      asset,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/assets/:id/remove
 * Remove an asset (confirmed violation)
 */
router.post('/assets/:id/remove', async (req, res, next) => {
  try {
    const { reason, reportId } = req.body;

    if (!reason || reason.trim().length < 10) {
      throw new ValidationError('Reason is required (minimum 10 characters)');
    }

    // Get current asset status before updating
    const currentResult = await query(`
      SELECT status FROM assets WHERE id = $1
    `, [req.params.id]);

    if (currentResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const oldStatus = currentResult.rows[0].status;

    const result = await query(`
      UPDATE assets
      SET status = 'removed',
          removed_reason = $1,
          removed_by = $2,
          removed_at = NOW(),
          updated_at = NOW()
      WHERE id = $3
      RETURNING id, name, slug, status
    `, [sanitizePlainText(reason, 1000), req.user.id, req.params.id]);

    const asset = result.rows[0];

    await logModAction(req.user.id, 'remove_asset', 'asset', asset.id, reason, null, {
      oldStatus,
      reportId: reportId || null,
    });

    // If there's an associated report, mark it as actioned
    if (reportId) {
      await query(`
        UPDATE abuse_reports
        SET status = 'actioned',
            resolved_by = $1,
            resolved_at = NOW()
        WHERE id = $2
      `, [req.user.id, reportId]);
    }

    logAuditEvent('asset_removed', 'MODERATION', req, {
      assetId: asset.id,
      assetSlug: asset.slug,
    });

    res.json({
      success: true,
      message: 'Asset removed',
      asset,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/assets/:id/restore
 * Restore a hidden/removed asset
 */
router.post('/assets/:id/restore', async (req, res, next) => {
  try {
    const { notes } = req.body;

    // Get current status
    const currentResult = await query(`
      SELECT status, removed_reason FROM assets WHERE id = $1
    `, [req.params.id]);

    if (currentResult.rows.length === 0) {
      throw new NotFoundError('Asset not found');
    }

    const oldStatus = currentResult.rows[0].status;

    if (oldStatus === 'published') {
      throw new ValidationError('Asset is already published');
    }

    const result = await query(`
      UPDATE assets
      SET status = 'published',
          removed_reason = NULL,
          removed_by = NULL,
          removed_at = NULL,
          updated_at = NOW()
      WHERE id = $1
      RETURNING id, name, slug, status
    `, [req.params.id]);

    const asset = result.rows[0];

    await logModAction(req.user.id, 'restore_asset', 'asset', asset.id, 'Asset restored', notes, {
      oldStatus,
      oldReason: currentResult.rows[0].removed_reason,
    });

    logAuditEvent('asset_restored', 'MODERATION', req, {
      assetId: asset.id,
      assetSlug: asset.slug,
    });

    res.json({
      success: true,
      message: 'Asset restored',
      asset,
    });
  } catch (error) {
    next(error);
  }
});

// ============================================
// USER MODERATION
// ============================================

/**
 * GET /moderation/users
 * List users with optional status/role filter
 */
router.get('/users', async (req, res, next) => {
  try {
    const { status, role, limit = 50, offset = 0, search } = req.query;
    const validStatuses = ['active', 'warned', 'suspended', 'banned'];
    const validRoles = ['owner', 'admin', 'moderator', 'user'];

    let whereClause = '1=1';
    const params = [];
    let paramIndex = 1;

    if (status && validStatuses.includes(status)) {
      // Handle NULL status as 'active'
      if (status === 'active') {
        whereClause += ` AND (status = $${paramIndex} OR status IS NULL)`;
      } else {
        whereClause += ` AND status = $${paramIndex}`;
      }
      params.push(status);
      paramIndex++;
    }

    if (role && validRoles.includes(role)) {
      // Handle NULL role as 'user'
      if (role === 'user') {
        whereClause += ` AND (role = $${paramIndex} OR role IS NULL)`;
      } else {
        whereClause += ` AND role = $${paramIndex}`;
      }
      params.push(role);
      paramIndex++;
    }

    if (search) {
      whereClause += ` AND (username ILIKE $${paramIndex} OR email ILIKE $${paramIndex} OR display_name ILIKE $${paramIndex})`;
      params.push(`%${search}%`);
      paramIndex++;
    }

    params.push(Math.min(parseInt(limit) || 50, 100));
    params.push(parseInt(offset) || 0);

    const result = await query(`
      SELECT
        id, username, display_name, email, avatar_url,
        COALESCE(role, 'user') as role,
        COALESCE(status, 'active') as status,
        COALESCE(strike_count, 0) as strike_count,
        suspended_until, ban_reason,
        COALESCE(asset_count, 0) as asset_count,
        COALESCE(download_count, 0) as download_count,
        is_verified,
        created_at, updated_at
      FROM users
      WHERE ${whereClause}
      ORDER BY created_at DESC
      LIMIT $${paramIndex++} OFFSET $${paramIndex}
    `, params);

    const countResult = await query(`
      SELECT COUNT(*) as total
      FROM users
      WHERE ${whereClause}
    `, params.slice(0, -2));

    res.json({
      users: result.rows,
      total: parseInt(countResult.rows[0].total),
      limit: parseInt(limit),
      offset: parseInt(offset),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /moderation/users/:id
 * Get detailed user info for moderation
 */
router.get('/users/:id', async (req, res, next) => {
  try {
    const userResult = await query(`
      SELECT
        id, username, display_name, email, avatar_url, bio, website,
        role, status, strike_count, suspended_until, ban_reason,
        asset_count, download_count, is_verified, is_admin,
        created_at, updated_at
      FROM users
      WHERE id = $1
    `, [req.params.id]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const user = userResult.rows[0];

    // Get recent mod actions against this user
    const actionsResult = await query(`
      SELECT
        ma.*,
        m.username as moderator_username
      FROM mod_actions ma
      JOIN users m ON ma.moderator_id = m.id
      WHERE ma.target_type = 'user' AND ma.target_id = $1
      ORDER BY ma.created_at DESC
      LIMIT 20
    `, [req.params.id]);

    // Get user's assets
    const assetsResult = await query(`
      SELECT id, name, slug, status, asset_type, download_count, created_at
      FROM assets
      WHERE owner_id = $1
      ORDER BY created_at DESC
      LIMIT 20
    `, [req.params.id]);

    // Get reports against this user
    const reportsResult = await query(`
      SELECT * FROM abuse_reports
      WHERE target_type = 'user' AND target_id = $1
      ORDER BY created_at DESC
      LIMIT 10
    `, [user.username]);

    res.json({
      user,
      modActions: actionsResult.rows,
      assets: assetsResult.rows,
      reports: reportsResult.rows,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/users/:id/warn
 * Issue a warning to a user (adds a strike)
 */
router.post('/users/:id/warn', async (req, res, next) => {
  try {
    const { reason, reportId } = req.body;

    if (!reason || reason.trim().length < 10) {
      throw new ValidationError('Reason is required (minimum 10 characters)');
    }

    // Check user exists and get current state
    const userResult = await query(`
      SELECT id, username, role, status, strike_count FROM users WHERE id = $1
    `, [req.params.id]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUser = userResult.rows[0];

    // Can't warn users with equal or higher role
    if (hasRole(targetUser.role, req.user.role)) {
      throw new ForbiddenError('Cannot warn users with equal or higher role');
    }

    const result = await query(`
      UPDATE users
      SET status = 'warned',
          strike_count = strike_count + 1,
          updated_at = NOW()
      WHERE id = $1
      RETURNING id, username, status, strike_count
    `, [req.params.id]);

    const user = result.rows[0];

    await logModAction(req.user.id, 'warn_user', 'user', user.id, reason, null, {
      oldStrikeCount: targetUser.strike_count,
      newStrikeCount: user.strike_count,
      reportId: reportId || null,
    });

    if (reportId) {
      await query(`
        UPDATE abuse_reports
        SET status = 'actioned',
            resolved_by = $1,
            resolved_at = NOW()
        WHERE id = $2
      `, [req.user.id, reportId]);
    }

    logAuditEvent('user_warned', 'MODERATION', req, {
      userId: user.id,
      username: user.username,
      strikeCount: user.strike_count,
    });

    res.json({
      success: true,
      message: 'Warning issued',
      user,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/users/:id/suspend
 * Temporarily suspend a user
 */
router.post('/users/:id/suspend', async (req, res, next) => {
  try {
    const { reason, duration, reportId } = req.body;

    if (!reason || reason.trim().length < 10) {
      throw new ValidationError('Reason is required (minimum 10 characters)');
    }

    // Duration in days (default 7, max 90)
    const durationDays = Math.min(Math.max(parseInt(duration) || 7, 1), 90);

    // Check user exists and get current state
    const userResult = await query(`
      SELECT id, username, role, status FROM users WHERE id = $1
    `, [req.params.id]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUser = userResult.rows[0];

    // Can't suspend users with equal or higher role (admins required for this)
    if (hasRole(targetUser.role, req.user.role)) {
      throw new ForbiddenError('Cannot suspend users with equal or higher role');
    }

    // Mods can't suspend - need admin
    if (!hasRole(req.user.role, 'admin')) {
      throw new ForbiddenError('Admin access required to suspend users');
    }

    const suspendedUntil = new Date();
    suspendedUntil.setDate(suspendedUntil.getDate() + durationDays);

    const result = await query(`
      UPDATE users
      SET status = 'suspended',
          suspended_until = $1,
          strike_count = strike_count + 1,
          updated_at = NOW()
      WHERE id = $2
      RETURNING id, username, status, suspended_until, strike_count
    `, [suspendedUntil, req.params.id]);

    const user = result.rows[0];

    await logModAction(req.user.id, 'suspend_user', 'user', user.id, reason, null, {
      durationDays,
      suspendedUntil: suspendedUntil.toISOString(),
      reportId: reportId || null,
    });

    if (reportId) {
      await query(`
        UPDATE abuse_reports
        SET status = 'actioned',
            resolved_by = $1,
            resolved_at = NOW()
        WHERE id = $2
      `, [req.user.id, reportId]);
    }

    logAuditEvent('user_suspended', 'MODERATION', req, {
      userId: user.id,
      username: user.username,
      durationDays,
    });

    res.json({
      success: true,
      message: `User suspended for ${durationDays} days`,
      user,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/users/:id/ban
 * Permanently ban a user
 */
router.post('/users/:id/ban', async (req, res, next) => {
  try {
    const { reason, reportId } = req.body;

    if (!reason || reason.trim().length < 10) {
      throw new ValidationError('Reason is required (minimum 10 characters)');
    }

    // Admin required for bans
    if (!hasRole(req.user.role, 'admin')) {
      throw new ForbiddenError('Admin access required to ban users');
    }

    // Check user exists and get current state
    const userResult = await query(`
      SELECT id, username, role, status FROM users WHERE id = $1
    `, [req.params.id]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUser = userResult.rows[0];

    // Can't ban users with equal or higher role
    if (hasRole(targetUser.role, req.user.role)) {
      throw new ForbiddenError('Cannot ban users with equal or higher role');
    }

    // Can't ban owner
    if (targetUser.role === 'owner') {
      throw new ForbiddenError('Cannot ban the owner');
    }

    const result = await query(`
      UPDATE users
      SET status = 'banned',
          ban_reason = $1,
          suspended_until = NULL,
          updated_at = NOW()
      WHERE id = $2
      RETURNING id, username, status, ban_reason
    `, [sanitizePlainText(reason, 500), req.params.id]);

    const user = result.rows[0];

    // Also hide all their assets
    await query(`
      UPDATE assets
      SET status = 'hidden',
          removed_reason = 'Owner banned',
          removed_by = $1,
          removed_at = NOW()
      WHERE owner_id = $2 AND status = 'published'
    `, [req.user.id, user.id]);

    await logModAction(req.user.id, 'ban_user', 'user', user.id, reason, null, {
      reportId: reportId || null,
    });

    if (reportId) {
      await query(`
        UPDATE abuse_reports
        SET status = 'actioned',
            resolved_by = $1,
            resolved_at = NOW()
        WHERE id = $2
      `, [req.user.id, reportId]);
    }

    logAuditEvent('user_banned', 'MODERATION', req, {
      userId: user.id,
      username: user.username,
    });

    res.json({
      success: true,
      message: 'User banned',
      user,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/users/:id/unban
 * Remove ban/suspension from a user
 */
router.post('/users/:id/unban', async (req, res, next) => {
  try {
    const { notes } = req.body;

    // Admin required
    if (!hasRole(req.user.role, 'admin')) {
      throw new ForbiddenError('Admin access required to unban users');
    }

    const userResult = await query(`
      SELECT id, username, status, ban_reason FROM users WHERE id = $1
    `, [req.params.id]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUser = userResult.rows[0];

    if (targetUser.status === 'active') {
      throw new ValidationError('User is not banned or suspended');
    }

    const result = await query(`
      UPDATE users
      SET status = 'active',
          ban_reason = NULL,
          suspended_until = NULL,
          updated_at = NOW()
      WHERE id = $1
      RETURNING id, username, status
    `, [req.params.id]);

    const user = result.rows[0];

    await logModAction(req.user.id, 'unban_user', 'user', user.id, 'Ban/suspension lifted', notes, {
      oldStatus: targetUser.status,
      oldBanReason: targetUser.ban_reason,
    });

    logAuditEvent('user_unbanned', 'MODERATION', req, {
      userId: user.id,
      username: user.username,
    });

    res.json({
      success: true,
      message: 'User unbanned',
      user,
    });
  } catch (error) {
    next(error);
  }
});

/**
 * POST /moderation/users/:id/role
 * Change a user's role (admin+ only)
 */
router.post('/users/:id/role', async (req, res, next) => {
  try {
    const { role, reason } = req.body;
    const validRoles = ['user', 'moderator', 'admin'];

    if (!role || !validRoles.includes(role)) {
      throw new ValidationError(`Role must be one of: ${validRoles.join(', ')}`);
    }

    if (!reason || reason.trim().length < 5) {
      throw new ValidationError('Reason is required');
    }

    // Check permissions
    const userResult = await query(`
      SELECT id, username, role FROM users WHERE id = $1
    `, [req.params.id]);

    if (userResult.rows.length === 0) {
      throw new NotFoundError('User not found');
    }

    const targetUser = userResult.rows[0];

    // Can't change owner role
    if (targetUser.role === 'owner') {
      throw new ForbiddenError('Cannot change owner role');
    }

    // Only owner can promote to admin
    if (role === 'admin' && req.user.role !== 'owner') {
      throw new ForbiddenError('Only owner can promote to admin');
    }

    // Only admin+ can change roles
    if (!hasRole(req.user.role, 'admin')) {
      throw new ForbiddenError('Admin access required to change roles');
    }

    // Can't change role of users with equal or higher role (unless owner)
    if (hasRole(targetUser.role, req.user.role) && req.user.role !== 'owner') {
      throw new ForbiddenError('Cannot change role of users with equal or higher role');
    }

    const result = await query(`
      UPDATE users
      SET role = $1,
          is_admin = $2,
          updated_at = NOW()
      WHERE id = $3
      RETURNING id, username, role
    `, [role, role === 'admin' || role === 'owner', req.params.id]);

    const user = result.rows[0];

    await logModAction(req.user.id, 'change_role', 'user', user.id, reason, null, {
      oldRole: targetUser.role,
      newRole: role,
    });

    logAuditEvent('user_role_changed', 'ADMIN', req, {
      userId: user.id,
      username: user.username,
      oldRole: targetUser.role,
      newRole: role,
    });

    res.json({
      success: true,
      message: `User role changed to ${role}`,
      user,
    });
  } catch (error) {
    next(error);
  }
});

// ============================================
// MOD ACTION HISTORY
// ============================================

/**
 * GET /moderation/actions
 * View moderation action history
 */
router.get('/actions', async (req, res, next) => {
  try {
    const { moderatorId, targetType, actionType, limit = 50, offset = 0 } = req.query;

    let whereClause = '1=1';
    const params = [];
    let paramIndex = 1;

    if (moderatorId) {
      whereClause += ` AND ma.moderator_id = $${paramIndex++}`;
      params.push(parseInt(moderatorId));
    }

    if (targetType && ['asset', 'user', 'report'].includes(targetType)) {
      whereClause += ` AND ma.target_type = $${paramIndex++}`;
      params.push(targetType);
    }

    if (actionType) {
      whereClause += ` AND ma.action_type = $${paramIndex++}`;
      params.push(actionType);
    }

    params.push(Math.min(parseInt(limit) || 50, 100));
    params.push(parseInt(offset) || 0);

    const result = await query(`
      SELECT
        ma.*,
        m.username as moderator_username,
        CASE
          WHEN ma.target_type = 'user' THEN (
            SELECT username FROM users WHERE id = ma.target_id
          )
          WHEN ma.target_type = 'asset' THEN (
            SELECT u.username || '/' || a.slug
            FROM assets a
            JOIN users u ON a.owner_id = u.id
            WHERE a.id = ma.target_id
          )
        END as target_name
      FROM mod_actions ma
      JOIN users m ON ma.moderator_id = m.id
      WHERE ${whereClause}
      ORDER BY ma.created_at DESC
      LIMIT $${paramIndex++} OFFSET $${paramIndex}
    `, params);

    const countResult = await query(`
      SELECT COUNT(*) as total
      FROM mod_actions ma
      WHERE ${whereClause}
    `, params.slice(0, -2));

    res.json({
      actions: result.rows,
      total: parseInt(countResult.rows[0].total),
      limit: parseInt(limit),
      offset: parseInt(offset),
    });
  } catch (error) {
    next(error);
  }
});

/**
 * GET /moderation/stats
 * Get moderation statistics
 */
router.get('/stats', async (req, res, next) => {
  try {
    const result = await query(`
      SELECT
        -- Report stats
        (SELECT COUNT(*) FROM abuse_reports WHERE status = 'pending') as pending_reports,
        (SELECT COUNT(*) FROM abuse_reports WHERE status = 'actioned') as actioned_reports,
        (SELECT COUNT(*) FROM abuse_reports WHERE status = 'dismissed') as dismissed_reports,

        -- Asset stats
        (SELECT COUNT(*) FROM assets WHERE status = 'published') as published_assets,
        (SELECT COUNT(*) FROM assets WHERE status = 'hidden') as hidden_assets,
        (SELECT COUNT(*) FROM assets WHERE status = 'removed') as removed_assets,
        (SELECT COUNT(*) FROM assets WHERE status = 'under_review') as review_assets,

        -- User stats
        (SELECT COUNT(*) FROM users WHERE status = 'active') as active_users,
        (SELECT COUNT(*) FROM users WHERE status = 'warned') as warned_users,
        (SELECT COUNT(*) FROM users WHERE status = 'suspended') as suspended_users,
        (SELECT COUNT(*) FROM users WHERE status = 'banned') as banned_users,

        -- Mod action stats (last 7 days)
        (SELECT COUNT(*) FROM mod_actions WHERE created_at > NOW() - INTERVAL '7 days') as actions_last_week,
        (SELECT COUNT(*) FROM mod_actions WHERE created_at > NOW() - INTERVAL '24 hours') as actions_last_day
    `);

    // Get action breakdown by type (last 7 days)
    const actionBreakdown = await query(`
      SELECT action_type, COUNT(*) as count
      FROM mod_actions
      WHERE created_at > NOW() - INTERVAL '7 days'
      GROUP BY action_type
      ORDER BY count DESC
    `);

    // Get report breakdown by reason
    const reportBreakdown = await query(`
      SELECT reason, status, COUNT(*) as count
      FROM abuse_reports
      WHERE created_at > NOW() - INTERVAL '30 days'
      GROUP BY reason, status
      ORDER BY count DESC
    `);

    res.json({
      overview: result.rows[0],
      actionBreakdown: actionBreakdown.rows,
      reportBreakdown: reportBreakdown.rows,
    });
  } catch (error) {
    next(error);
  }
});

export default router;
