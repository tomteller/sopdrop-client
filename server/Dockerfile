FROM node:20-alpine

WORKDIR /app

# Copy package files first for better layer caching
COPY package.json ./

# Install production dependencies only
RUN npm install --omit=dev

# Copy application source
COPY . .

# Create library directories for local fallback (not used with R2)
RUN mkdir -p library/nodes library/hdas library/temp library/avatars library/thumbnails library/media library/drafts

EXPOSE 8080

CMD ["node", "src/index.js"]
