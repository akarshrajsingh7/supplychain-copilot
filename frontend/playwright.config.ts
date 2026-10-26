import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests',timeout:60000,use:{baseURL:'http://127.0.0.1:5173',viewport:{width:1600,height:1050}},reporter:'list'});
