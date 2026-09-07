FROM node:18-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci

# Copy source code
COPY . .

# Expose the port the app runs on
EXPOSE 5173

# Command to run the application in development
CMD ["npm", "run", "dev", "--", "--host"]