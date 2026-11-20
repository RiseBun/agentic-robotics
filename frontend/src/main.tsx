import React from 'react';
import { createRoot } from 'react-dom/client';
import { RobotWorkbench } from './RobotWorkbench';
import './robot.css';

createRoot(document.getElementById('root')!).render(<React.StrictMode><RobotWorkbench /></React.StrictMode>);
